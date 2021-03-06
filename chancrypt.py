# vim: set fileencoding=utf-8 :
#
# chancrypt.py
#
# Allows encrypted messaging in an irc channel with a pre-shared key
#
# The author would like to thank:
#
# - Nicolai Lissner for his crypt.py which provided an excellent starting point
#   - https://weechat.org/scripts/source/crypt.py.html/
# - Alon Swartz for his blog post at  on python-crypto
#   - http://www.turnkeylinux.org/blog/python-symmetric-encryption
#

SCRIPT_NAME = "chancrypt"
SCRIPT_AUTHOR = "Colin Moller <colin@unixarmy.com>"
SCRIPT_VERSION = "0.9.3"
SCRIPT_LICENSE = "BSD"
SCRIPT_DESC = "Allows encrypted messaging in an irc channel with" \
              "a pre-shared key"

import string
import re

import zlib
import struct
from base64 import b64encode, b64decode

from Crypto.Cipher import AES
from Crypto import Random
from Crypto.Protocol.KDF import PBKDF2

script_options = {
    "message_indicator": "⚷"
}

channel_prefixes = ["#", "&"]

cipher_mode = AES.MODE_CTR

class CheckSumError(Exception):
    pass


def generate_key_from_passphrase(secret):
    """pads secret to AES-256 block size"""
    # We set a low-ish number of iterations to prevent
    # CPU spikes when sending frequent messages.
    iterations = 1000
    salt = "SaltedPasswordsAreTasty"

    return PBKDF2(secret, salt, dkLen=32, count=iterations)


def generate_aes_key():
    rnd = Random.OSRNG.posix.new().read(AES.block_size)
    return rnd


def encrypt(plaintext, secret, checksum=True):
    """encrypt plaintext with secret
    plaintext   - content to encrypt
    secret      - secret to encrypt plaintext
    checksum    - attach crc32 byte encoded (default: True)
    returns base64 encoded zlib compressed iv + ciphertext
    """

    secret = generate_key_from_passphrase(secret)
    iv = generate_aes_key()
    encobj = AES.new(secret, cipher_mode, iv)

    if checksum:
        plaintext += struct.pack("=i", zlib.crc32(plaintext))

    return b64encode(
        zlib.compress(
            iv + encobj.encrypt(plaintext)
        )
    )


def decrypt(encoded_ciphertext, secret, checksum=True):
    """decrypt ciphertext with secret
    encoded_ciphertext  - base64 encoded compressed iv + ciphertext to decrypt
    secret      - secret to decrypt ciphertext
    checksum    - verify crc32 byte encoded checksum (default: True)
    returns plaintext
    """

    secret = generate_key_from_passphrase(secret)

    ciphertext_with_iv = zlib.decompress(
        b64decode(encoded_ciphertext)
    )

    iv = ciphertext_with_iv[:AES.block_size]
    ciphertext = ciphertext_with_iv[AES.block_size:]

    encobj = AES.new(secret, cipher_mode, iv)

    plaintext = encobj.decrypt(ciphertext)

    if checksum:
        crc, plaintext = (plaintext[-4:], plaintext[:-4])
        if not crc == struct.pack("=i", zlib.crc32(plaintext)):
            raise CheckSumError("checksum mismatch")

    return plaintext


def get_key_for_channel(server, channel):

    config_location = "passphrase.%s.%s" % (server, channel)
    config_prefix = "plugins.var.python.chancrypt"
    channel_key = weechat.config_get_plugin(config_location)

    if len(channel_key) < 1 or channel_key is None:
        weechat.prnt("", "Recieved an encrypted message, but passphrase is"
                         " not set for channel %s on network %s"
                         % (channel, server))
        weechat.prnt("", "Use '/set %s.%s SOME_KEY' to enable encryption."
                         % (config_prefix, config_location))
        return None

    return channel_key


def weechat_msg_decrypt(data, msgtype, servername, args):
    hostmask, chanmsg = string.split(args, "PRIVMSG ", 1)
    channelname, message = string.split(chanmsg, " :", 1)

    # TODO: is this necessary?
    if re.match(r'^\[\d{2}:\d{2}:\d{2}]\s', message):
        timestamp = message[:11]
        message = message[11:]
    else:
        timestamp = ''

    # check to see if this message has our prefix
    # if not, don't try to decode it
    if message[:5] != "!ENC ":
        return args

    message = message[5:]

    if channelname[0] in channel_prefixes:
        username = channelname
    else:
        username, rest = string.split(hostmask, "!", 1)
        username = username[1:]

    channel_key = get_key_for_channel(servername, username)
    if channel_key is None:
        return args

    # decrypt message
    try:
        decrypted = decrypt(message, channel_key)
        return hostmask \
            + "PRIVMSG " \
            + channelname \
            + " :" + chr(3) + "09" \
            + weechat.config_get_plugin("message_indicator") + " " \
            + chr(15) + timestamp + decrypted
    except CheckSumError:
        decrypted = "(Invalid message - Incorrect passphrase)"
        return hostmask \
            + "PRIVMSG " \
            + channelname  \
            + " :" + chr(3) + "04" \
            + weechat.config_get_plugin("message_indicator") + " " \
            + chr(15) + timestamp + decrypted


def weechat_msg_encrypt(data, msgtype, servername, args):
    pre, message = string.split(args, ":", 1)

    hostmask, chanmsg = string.split(args, "PRIVMSG ", 1)
    channelname, message = string.split(chanmsg, " :", 1)

    channel_key = get_key_for_channel(servername, channelname)

    if channel_key is None:
        return args

    # encrypt message
    encrypted = encrypt(message, channel_key)

    returning = pre + ":" + "!ENC " + encrypted
    return returning


# register script with weechat, set config
if __name__ == "__main__":
    try:
        import weechat
        if weechat.register(
                SCRIPT_NAME, SCRIPT_AUTHOR, SCRIPT_VERSION,
                SCRIPT_LICENSE, SCRIPT_DESC, "", "UTF-8"):
            weechat_dir = weechat.info_get("weechat_dir", "")
            version = weechat.info_get("version_number", "") or 0
            if int(version) < 0x00030000:
                weechat.prnt("", "%s%s: WeeChat 0.3.0 is"
                                 " required for this script."
                             % (weechat.prefix("error"), SCRIPT_NAME))

            else:
                for option, default_value in script_options.iteritems():
                    if not weechat.config_is_set_plugin(option):
                        weechat.config_set_plugin(option, default_value)

                weechat.hook_modifier("irc_in_privmsg",
                                      "weechat_msg_decrypt", "")
                weechat.hook_modifier("irc_out_privmsg",
                                      "weechat_msg_encrypt", "")
    except ImportError:
        # not running under Weechat, run a simple test instead
        print "Running simple tests"
        # TODO: run some simple tests
        pass
