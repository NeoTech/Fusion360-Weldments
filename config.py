# Application Global Variables
# This module serves as a way to share variables across different
# modules (global variables).

import hashlib
import os
import shutil

# Flag that indicates to run in Debug mode or not. When running in Debug mode
# more information is written to the Text Command window. Generally, it's useful
# to set this to True while developing an add-in and set it to False when you
# are ready to distribute it.
DEBUG = True

# Gets the name of the add-in from the name of the folder the py file is in.
# This is used when defining unique internal names for various UI elements 
# that need a unique name. It's also recommended to use a company name as 
# part of the ID to better ensure the ID is unique.
ADDIN_NAME = os.path.basename(os.path.dirname(__file__))
COMPANY_NAME = 'ACME'

# Palettes
sample_palette_id = f'{COMPANY_NAME}_{ADDIN_NAME}_palette_id'


def icon_folder(module_file):
    """Return a ribbon-icon folder whose *path* changes when the icons change.

    Fusion caches a command's toolbar bitmap by its absolute resource-folder
    path for the whole process, so editing the PNGs on disk and reloading the
    add-in leaves the stale icon until Fusion restarts.  To bust that cache we
    mirror the command's ``resources/*.png`` into a sibling folder named by a
    hash of their bytes and hand that path to ``addButtonDefinition``.  Same
    icons -> same path (cache hit, no churn); edited icons -> new path (fresh
    bitmap on the next reload, no restart).
    """
    src = os.path.join(os.path.dirname(os.path.abspath(module_file)), 'resources')
    names = ('16x16.png', '32x32.png', '64x64.png')
    digest = hashlib.sha1()
    for n in names:
        p = os.path.join(src, n)
        digest.update(n.encode())
        if os.path.exists(p):
            with open(p, 'rb') as fh:
                digest.update(fh.read())
    tag = digest.hexdigest()[:12]
    dst = os.path.join(src, '_v' + tag)
    if not os.path.isdir(dst):
        os.makedirs(dst, exist_ok=True)
        for n in names:
            s = os.path.join(src, n)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(dst, n))
    return dst + os.sep