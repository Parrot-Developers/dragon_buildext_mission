import os
import dragon
import json
import logging
import shutil
import tempfile

from task import TaskError as TaskError

try:
    from dragon_buildext_sign.buildext import sign_archive
    CAN_SIGN = True
except ImportError:
    CAN_SIGN = False

VERSION_SERVER_URL = "noserver"
PARTNER_SERVER_URL = "ftp://ftp2.parrot.biz"

SDK_TAR_NAME = "sdk.tar.gz"
SDK_TAR_PATH = os.path.join(dragon.WORKSPACE_DIR, SDK_TAR_NAME)
SDK_DIR_PATH = os.path.join(dragon.WORKSPACE_DIR, "sdk")

DEFAULT_BASE_SDK_PRODUCT = "anafi2"
DEFAULT_BASE_SDK_VARIANT = "%s_airsdk"

# Release mode: override the parrot build project property to publish all
# missions under the same folder
if os.environ.get("JKS_RELEASE_VERSION", None) is not None:
    dragon.PARROT_BUILD_PROP_PROJECT = "airsdk-missions"

#===============================================================================
#===============================================================================
def download_file(url, outdirpath, netrc_path=None):
    logging.info("Downloading file: %s", url)
    file_name = os.path.join(outdirpath, os.path.basename(url))

    netrc_option = ""
    if netrc_path is not None and os.path.exists(netrc_path):
        netrc_option = "--netrc-file %s" % netrc_path

    dragon.exec_cmd("curl %s --progress-bar %s --output %s" % (netrc_option,
        url, file_name))

#===============================================================================
#===============================================================================
def get_root_url(base_sdk_product, base_sdk_variant, base_sdk_version, netrc_path=None):
    # partners server
    if netrc_path is not None and os.path.exists(netrc_path):
        return "%s/versions/%s/%s/%s" % (PARTNER_SERVER_URL, base_sdk_product,
                    base_sdk_variant, base_sdk_version)

    # version server
    return "%s/versions/projects/%s/%s-%s/%s/bin" % (VERSION_SERVER_URL, base_sdk_product,
                base_sdk_product, base_sdk_variant, base_sdk_version)

#===============================================================================
#===============================================================================
def get_local_key_path():
    return os.path.join(dragon.PRODUCT_DIR, "common", "key.pem")

def get_remote_key_path():
    return ""

def sign(tar, filelist):
    # Try signing the archive with local key
    key = "ecdsa:local:" + get_local_key_path()
    if os.path.exists(get_local_key_path()):
        logging.warning("Signing archive with local key: %s" % key)
        sign_archive(tar, filelist, key, "signature.ecdsa", "sha512")
        return
    else:
        logging.warning("No local key found in %s" % key)

    # Try signing the archive with remote key
    key = "ecdsa:remote:" + get_remote_key_path()
    if get_remote_key_path() != "":
        logging.warning("Signing archive with remote key: %s", key)
        sign_archive(tar, filelist, key, "signature.ecdsa-dev", "sha512")
    else:
        logging.warning("No remote key found in %s" % key)

#===============================================================================
#===============================================================================
def gen_archive(mission_dir):
    name = os.path.split(mission_dir)[1]
    logging.info("Generating mission archive for '%s'", name)

    # Files to put in archive and sign
    filelist = [
        "mission.json",
        "payload.tar.gz",
    ]

    with tempfile.TemporaryDirectory(prefix="missions-") as tmpdir:
        mission_tar = os.path.join(tmpdir, name + ".tar")
        mission_tar_gz = os.path.join(tmpdir, name + ".tar.gz")

        # Create payload.tar.gz
        cmd = "tar -C %s -czvf %s ." % (
                os.path.join(mission_dir, "payload"),
                os.path.join(tmpdir, "payload.tar.gz"))
        dragon.exec_cmd(cmd)

        # Copy mission.json
        cmd = "cp -pf %s %s" % (
                os.path.join(mission_dir, "mission.json"),
                os.path.join(tmpdir, "mission.json"))
        dragon.exec_cmd(cmd)

        # Create the mission archive (not compressed yet)
        cmd = "tar -C %s -cvf %s %s" % (
                tmpdir,
                mission_tar,
                " ".join(filelist))
        dragon.exec_cmd(cmd)

        if CAN_SIGN:
            sign(mission_tar, filelist)
        else:
            logging.warning("No signing tools available")

        # Compress and copy to image directory
        dragon.exec_cmd("gzip %s" % mission_tar)
        dragon.exec_cmd("cp -pf %s %s/" % (mission_tar_gz, dragon.IMAGES_DIR))

#===============================================================================
#===============================================================================
def set_target_version(json_cfg, json_cfg_var, env_var, magic_var):
    if not json_cfg:
        return

    version = None
    if json_cfg_var in json_cfg:
        version = json_cfg[json_cfg_var]

    if os.getenv(env_var):
        json_cfg[json_cfg_var] = os.getenv(env_var)
    elif not version or version == magic_var:
        # TODO get the basesdk/airsdk version here (after Alchemy dev is done)
        json_cfg[json_cfg_var] = dragon.PARROT_BUILD_PROP_VERSION

#===============================================================================
#===============================================================================
def set_versions(mission_dir):
    json_path = os.path.join(mission_dir, "mission.json")
    json_cfg = None
    with open(json_path, "r") as fd:
        try:
            json_cfg = json.load(fd)
        except ValueError as ex:
            raise TaskError("Error while parsing json file %s: %s" %
                    (json_path, str(ex)))

    # mission version
    json_cfg['version'] = dragon.PARROT_BUILD_PROP_VERSION

    # firmware target min/max versions
    set_target_version(json_cfg, 'target_min_version',
        'PARROT_BUILD_FIRMWARE_VERSION_MIN', '@CURRENT_TARGET_FIRMWARE_VERSION')

    set_target_version(json_cfg, 'target_max_version',
        'PARROT_BUILD_FIRMWARE_VERSION_MAX', '@CURRENT_TARGET_FIRMWARE_VERSION')

    with open(json_path, "w") as fd:
        try:
            json.dump(json_cfg, fd, indent=4, sort_keys=True)
        except ValueError as ex:
            raise TaskError("Error while writing json file %s: %s" %
                    (json_path, str(ex)))

#===============================================================================
#===============================================================================
def gen_final(mission_dir):
    name = os.path.split(mission_dir)[1]
    logging.info("Generating mission final for '%s'", name)

    dirslist = {
        "etc": "etc",
        "lib": "lib",
        "usr/lib/python/site-packages": "python",
        "usr/lib": "lib",
        "usr/share": "share",
    }

    # Clean directories that may have been copied twice
    cleandirslist = [
        "lib/python",
        "lib/python3.[0-9]",
    ]

    for key in dirslist:
        # src dir
        src_path = os.path.join(dragon.FINAL_DIR, key)
        if not os.path.exists(src_path):
            continue
        src_entries = os.listdir(src_path)
        if len(src_entries) == 0:
            continue
        for entry in src_entries:
            if os.path.isdir(entry) and len(os.listdir(entry)) == 0:
                os.rmdir(entry)
        # dst dir
        dst_path = os.path.join(dragon.FINAL_DIR, mission_dir, "payload", dirslist[key])
        dragon.makedirs(dst_path)
        # move src to dst dir
        dragon.exec_cmd("cp -av %s/* %s" % (src_path, dst_path))

    for key in cleandirslist:
        dir_path = os.path.join(dragon.FINAL_DIR, mission_dir, "payload", key)
        dragon.exec_cmd("rm -rfv %s" % dir_path)

#===============================================================================
# Hooks.
#===============================================================================
def hook_pre_download_base_sdk(task, args):
    if os.path.exists(SDK_TAR_PATH):
        os.unlink(SDK_TAR_PATH)

    sdk_variant_dir_path = os.path.join(SDK_DIR_PATH, dragon.VARIANT)
    if os.path.exists(sdk_variant_dir_path):
        if os.path.isdir(sdk_variant_dir_path):
            shutil.rmtree(sdk_variant_dir_path)
        else:
            os.unlink(sdk_variant_dir_path)

    os.makedirs(sdk_variant_dir_path)

def hook_download_base_sdk(task, args):
    base_sdk_product = os.getenv("PARROT_BUILD_BASE_SDK_PRODUCT", DEFAULT_BASE_SDK_PRODUCT)
    base_sdk_variant = os.getenv("PARROT_BUILD_BASE_SDK_VARIANT",
                            DEFAULT_BASE_SDK_VARIANT % dragon.VARIANT)
    base_sdk_version = os.getenv("PARROT_BUILD_BASE_SDK_VERSION", None)

    if base_sdk_version is None or base_sdk_version == "latest":
        base_sdk_version = r"%23latest"

    # path to .netrc file (mandatory for partners)
    netrc_path = os.path.join(dragon.WORKSPACE_DIR, ".netrc")

    # get urls
    root_url = get_root_url(base_sdk_product, base_sdk_variant,
                    base_sdk_version, netrc_path)
    sdk_url = "%s/%s" % (root_url, SDK_TAR_NAME)

    # download sdk
    download_file(sdk_url, outdirpath=dragon.WORKSPACE_DIR, netrc_path=netrc_path)

    # extract sdk
    sdk_variant_dir_path = os.path.join(SDK_DIR_PATH, dragon.VARIANT)
    logging.info("Extracting %s into %s", SDK_TAR_NAME, sdk_variant_dir_path)
    dragon.exec_cmd("tar -xf %s -C %s --strip 1" % (SDK_TAR_PATH, sdk_variant_dir_path))

def hook_post_images(task, args):
    task.call_base_post_hook(args)

    missions_dir = os.path.join(dragon.FINAL_DIR, "missions")
    if not os.path.exists(missions_dir):
        return

    for entry in os.listdir(missions_dir):
        mission_dir = os.path.join(missions_dir, entry)
        if os.path.isdir(mission_dir):
            gen_final(mission_dir)
            set_versions(mission_dir)
            gen_archive(mission_dir)

#===============================================================================
#===============================================================================
def setup_deftasks():
    dragon.override_meta_task("images",
        posthook=hook_post_images,
    )

    dragon.override_meta_task("images-all",
        posthook=hook_post_images,
    )

    dragon.add_meta_task(
        name="download-base-sdk",
        desc="Download a base SDK either from version (internal) or partner servers",
        prehook=hook_pre_download_base_sdk,
        exechook=hook_download_base_sdk,
        weak=True
    )
