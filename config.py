# config.py
#
# Copyright (c) 2017 Oak Ridge National Laboratory.
# All rights reserved.
# See file LICENSE that is included with this distribution.
#
# @author Klemen Vodopivec

import collections
import os
import yaml
import sys

import common

# This dictionary defines all valid configuration options   
default_cfg = {
    "tornado": {
        "debug": False
    },
    "server": {
        "port": 8888,
        "threads": 1,
        "redirects": {}
    },
    "static_web": {
        "path": "static/"
    },
    "convert": {
        "use_cache": True,
        "files": { "path": "orig/" },
        "cache": { "path": "cache/" },
        "opi": { "templates": "templates/opi/" },
        "bob": { "templates": "templates/bob/" }
    }
}

def getAbsPath(path):
    """ Make path absolute.

    Relative paths are converte to absolute path with a base folder
    being location where script was executed from.

    All paths are normalized, that is /home/../var/log resolves to /var/log.
    """
    if not os.path.isabs(path):
        pwd = os.path.abspath( os.path.dirname(sys.argv[0]) )
        path = os.path.join(pwd, path)
    return os.path.normpath(path)

def dictMerge(defaults, overrides):
    """ Like dict.update() but works recursively into sub-dictionaries. Returns new dictionary. """
    d = dict(defaults)
    for k, v in overrides.iteritems():
        if k in defaults and isinstance(defaults[k], dict) and isinstance(overrides[k], collections.Mapping):
            d[k] = dictMerge(defaults[k], overrides[k])
        else:
            d[k] = overrides[k]
    return d

def load(path):
    try:
        f = open(path, "r")
    except:
        raise common.ebEpicsError("Failed to open configuration file: {0}".format(str(e)))
    try:
        cfg = yaml.safe_load(f)
        f.close()
    except Exception, e:
        f.close()
        raise common.WebEpicsError("Failed to parse configuration file: {0}".format(e))

    # Merge user config with defaults
    cfg = dictMerge(default_cfg, cfg)

    # Now do some path checking
    # TODO: This needs to be more generic and check all paths
    if "static_web" not in cfg:
        cfg = { "static_web": {} }
    cfg["static_web"]["path"] = getAbsPath(cfg["static_web"]["path"] if "path" in cfg["static_web"] else "static")

    return cfg