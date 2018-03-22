"""
py2app build script for Electrum Bitcoin Private

Usage (Mac OS X):
     python setup.py py2app
"""

from setuptools import setup
from plistlib import Plist
import requests
import os
import shutil

from lib.version import ELECTRUM_VERSION as version

CERT_PATH = requests.certs.where()

name = "Electrum SNG"
mainscript = 'electrum'

plist = Plist.fromFile('Info.plist')
plist.update(dict(CFBundleIconFile='icons/electrum.icns'))


os.environ["REQUESTS_CA_BUNDLE"] = "cacert.pem"
shutil.copy(mainscript, mainscript + '.py')
mainscript += '.py'
extra_options = dict(
    setup_requires=['py2app'],
    app=[mainscript],
    packages=[
        'electrum-sng',
        'electrum-sng_gui',
        'electrum-sng_gui.qt',
        'electrum-sng_plugins',
        'electrum-sng_plugins.audio_modem',
        'electrum-sng_plugins.cosigner_pool',
        'electrum-sng_plugins.email_requests',
        'electrum-sng_plugins.greenaddress_instant',
        'electrum-sng_plugins.hw_wallet',
        'electrum-sng_plugins.keepkey',
        'electrum-sng_plugins.labels',
        'electrum-sng_plugins.ledger',
        'electrum-sng_plugins.trezor',
        'electrum-sng_plugins.digitalbitbox',
        'electrum-sng_plugins.trustedcoin',
        'electrum-sng_plugins.virtualkeyboard',

    ],
    package_dir={
        'electrum-sng': 'lib',
        'electrum-sng_gui': 'gui',
        'electrum-sng_plugins': 'plugins'
    },
    data_files=[CERT_PATH],
    options=dict(py2app=dict(argv_emulation=False,
                             includes=['sip'],
                             packages=['lib', 'gui', 'plugins'],
                             iconfile='icons/electrum.icns',
                             plist=plist,
                             resources=["icons"])),
)

setup(
    name=name,
    version=version,
    **extra_options
)

# Remove the copied py file
os.remove(mainscript)
