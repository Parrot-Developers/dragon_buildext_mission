#!/usr/bin/env python3

import argparse
import binascii
import hashlib
import logging
import os
import requests
import socket
import subprocess
import tempfile
import time
import urllib

_DESCRIPTION = """
    Manage user specific mission keys.
"""

_APC_BASE_URL = "https://accounts.parrot.com"
_APC_TMP_ACCOUNT = "/V4/account/tmp/create"

_APC_CALLER_ID = "OpenFlight"
_APC_CALLER_KEY = "g%2SW+m,cc9|eDQBgK:qTS2l=;[O~f@W"

_ACADEMY_BASE_URL = "https://academy.parrot.com"
_ACADEMY_GENERATE_CHALLENGE = "/apiv1/4g/secrets/challenge"
_ACADEMY_COMPLETE_CHALLENGE = "/apiv1/4g/secrets"
_ACADEMY_API_KEY = "cd7oG8K9h86oCya0u5C0H7mphOuu8LU91o1hBLiG"

_DRONE_ADDRESS = "anafi-ai.local"
_DRONE_PORT = 80
_DRONE_SIGN_CHALENGE = "/api/v1/secure-element/sign_challenge"
_DRONE_PROPERTIES = "/api/v1/info/properties"

_DRONE_SECRET_DIR = os.path.expanduser("~/.parrot/anafi-ai")

_DRONE_USER_ID = 4
_DRONE_SMAC_FILENAME = f"user{_DRONE_USER_ID}_smac.aes"
_DRONE_SENC_FILENAME = f"user{_DRONE_USER_ID}_senc.aes"


def apc_get_signature(data, apc_key):
    """
    Generate a signature token to be used during the query to APC.
    data: dict with data that will be sent in the request.
    apc_key: api key to use.
    """
    ts = int(time.time())
    s = "".join(f"{data[k]}" for k in sorted(data.keys())) + f"{ts}" + apc_key
    token = hashlib.md5(s.encode("UTF-8")).hexdigest()
    return { "ts": ts, "token": token }


def apc_do_post_query(url, data):
    """
    Execute a POST request to APC.
    url: url of the request.
    data: dict with data that will be sent in the request.
    """
    apc_signature = apc_get_signature(data, _APC_CALLER_KEY)
    response = requests.post(
         url,
         data=data,
         headers={
             'X-CallerId': _APC_CALLER_ID,
        },
        params=apc_signature
    )
    response.raise_for_status()
    return response


def apc_create_tmp_user():
    """
    Create a temporary user in APC and return a token that can further be used
    as authentication.
    """
    url = _APC_BASE_URL + _APC_TMP_ACCOUNT
    response = apc_do_post_query(url, {})
    return response.json().get("apcToken")


def academy_generate_challenge(apc_token, operation):
    """
    Generate a challenge to be completed by the drone.
    apc_token: APC authentication token.
    operation: operation to perform ('get_secret').
    Return a challenge to be sent to the drone for completion.
    """
    url = _ACADEMY_BASE_URL + _ACADEMY_GENERATE_CHALLENGE
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {apc_token}",
            "X-Api-Key": _ACADEMY_API_KEY,
        },
        params={
            "operation": operation,
        }
    )
    response.raise_for_status()
    return response.text.strip()


def academy_complete_challenge(apc_token, message):
    """
    Complete a challenge request with the answer message from the drone
    apc_token: APC authentication token.
    message: response from drone to the challenge.
    Return final response of the request.
    """
    url = _ACADEMY_BASE_URL + _ACADEMY_COMPLETE_CHALLENGE
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {apc_token}",
            "X-Api-Key": _ACADEMY_API_KEY,
        },
        params={
            "message": message,
        }
    )
    response.raise_for_status()
    return response.json()


def drone_get_properties(base_url):
    """
    Get the drone properties.
    """
    url = base_url + _DRONE_PROPERTIES
    response = requests.get(url)
    response.raise_for_status()
    properties = response.json()
    return { prop["key"]: prop["value"] for prop in properties }


def drone_get_serial(base_url):
    """
    Get the drone serial number (PI....).
    """
    properties = drone_get_properties(base_url)
    return properties["ro.factory.serial"]


def drone_sign_challenge(base_url, operation, challenge):
    """
    Ask the drone to sign a challenge.
    operation: operation to sign.
    challenge: challenge to sign.
    Return the message wit th challenge response to be sent back to the server.
    """
    url = base_url + _DRONE_SIGN_CHALENGE
    response = requests.get(
        url,
        params={
            "operation": operation,
            "challenge": challenge,
        }
    )
    response.raise_for_status()
    return response.json().get("message")


def extract_drone_address(base_url):
    url = urllib.parse.urlparse(base_url)
    hostname = url.hostname or _DRONE_ADDRESS
    address = socket.gethostbyname(hostname)
    port = url.port or _DRONE_PORT
    return (address, port)


def drone_add_key(base_url, secret_dirpath, key_filepath):
    address, port = extract_drone_address(base_url)
    cmd = [
        "passe-muraille",
        "-H", address,
        "-p", str(port),
        "-k", secret_dirpath,
        "fm", "add", "file", key_filepath,
    ]
    subprocess.check_call(cmd)



def drone_remove_key(base_url, secret_dirpath, key_slot):
    address, port = extract_drone_address(base_url)
    cmd = [
        "passe-muraille",
        "-H", address,
        "-p", str(port),
        "-k", secret_dirpath,
        "fm", "remove", "slot", key_slot,
    ]
    subprocess.check_call(cmd)



def drone_list_keys(base_url, secret_dirpath):
    address, port = extract_drone_address(base_url)
    cmd = [
        "passe-muraille",
        "-H", address,
        "-p", str(port),
        "-k", secret_dirpath,
        "fm", "scan",
    ]
    subprocess.check_call(cmd)


def has_drone_secret_files(secret_dirpath):
    """
    Check if SENC/SMAC are available in the given directory.
    secret_dirpath: directory with secret files.
    """
    senc_filepath = os.path.join(secret_dirpath, _DRONE_SENC_FILENAME)
    smac_filepath = os.path.join(secret_dirpath, _DRONE_SMAC_FILENAME)
    return os.access(senc_filepath, os.R_OK) and os.access(smac_filepath, os.R_OK)


def save_drone_secret_files(secret_dirpath, smac, senc):
    """
    Save SENC/SMAC in given location.
    secret_dirpath: directory with secret files.
    smac: SMAC in binary form.
    senc: SENC in binary form.
    """
    senc_filepath = os.path.join(secret_dirpath, _DRONE_SENC_FILENAME)
    smac_filepath = os.path.join(secret_dirpath, _DRONE_SMAC_FILENAME)
    logging.info(f"Writing drone SENC/SMAC in '{secret_dirpath}'")

    os.makedirs(secret_dirpath, 0o700, exist_ok=True)
    with open(os.open(senc_filepath, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as fout:
        fout.write(senc)
    with open(os.open(smac_filepath, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as fout:
        fout.write(smac)


def do_work(options):
    drone_base_url = f"http://{options.drone_address}:{options.drone_port}"

    # Only generate APC authentication token if requested
    if options.gen_auth_token:
        logging.info("Creating temporary APC user and authentication token")
        apc_token = apc_create_tmp_user()
        print(apc_token)
        return

    logging.info("Retrieving drone serial number")
    drone_serial = drone_get_serial(drone_base_url)
    logging.info(f"-> {drone_serial}")
    tmpdir = None
    secret_dirpath = os.path.join(_DRONE_SECRET_DIR, drone_serial)

    if options.get_secret or not has_drone_secret_files(secret_dirpath):
        # Use given token or create a temporary one
        if options.auth_token:
            apc_token = options.auth_token
        else:
            logging.info("Creating temporary APC user and authentication token")
            apc_token =apc_create_tmp_user()

        logging.info("Generating challenge")
        challenge = academy_generate_challenge(apc_token, "get_secret")

        logging.info("Sending challenge to drone")
        message = drone_sign_challenge(drone_base_url, "get_secret", challenge)

        logging.info("Completing operation with drone response")
        secrets = academy_complete_challenge(apc_token, message)
        senc = binascii.a2b_hex(secrets["SENC"])
        smac = binascii.a2b_hex(secrets["SMAC"])

        # Save secret in either permanent location or temp one
        if not options.get_secret:
            tmpdir = tempfile.TemporaryDirectory()
            secret_dirpath = tmpdir.name
        save_drone_secret_files(secret_dirpath, smac, senc)

    if options.add_key:
        logging.info(f"Adding key '{options.add_key}' to the drone")
        drone_add_key(drone_base_url, secret_dirpath, options.add_key)

    if options.remove_key:
        logging.info(f"Removing key from slot '{options.remove_key}'")
        drone_remove_key(drone_base_url, secret_dirpath, options.remove_key)

    if options.list_keys:
        logging.info(f"Listing keys")
        drone_list_keys(drone_base_url, secret_dirpath)


def main():
    logging.getLogger().setLevel(logging.INFO)

    parser = argparse.ArgumentParser(description=_DESCRIPTION)

    parser.add_argument("--get-secret",
        action="store_true",
        help=f"Simply get drone secret and store it in '{_DRONE_SECRET_DIR}' for future use.")

    parser.add_argument("--add-key",
        metavar="FILE",
        help="Add new flight mission public key (in PEM format).")

    parser.add_argument("--remove-key",
        metavar="SLOT",
        help="Remove flight mission key from given slot.")

    parser.add_argument("--list-keys",
        action="store_true",
        help="List known flight mission keys.")

    parser.add_argument("--gen-auth-token",
        action="store_true",
        help="Simply generate an authentication token for future use.")

    parser.add_argument("--auth-token",
        metavar="TOKEN",
        help="Use specific authentication token instead of creating a temporary one.")

    parser.add_argument("--drone-address",
        metavar="ADDRESS",
        default=_DRONE_ADDRESS,
        help=f"Use specific drone adresss (default: {_DRONE_ADDRESS}).")

    parser.add_argument("--drone-port",
        metavar="PORT",
        type=int,
        default=_DRONE_PORT,
        help=f"Use specific drone port (default: {_DRONE_PORT}).")

    options = parser.parse_args()
    try:
        do_work(options)
    except Exception as ex:
        logging.error(f"Exception occured: {ex}")


if __name__ == "__main__":
    main()
