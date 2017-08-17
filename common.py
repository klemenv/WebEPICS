# common.py
#
# Copyright (c) 2017 Oak Ridge National Laboratory.
# All rights reserved.
# See file LICENSE that is included with this distribution.
#
# @author Klemen Vodopivec

class WebEpicsError(RuntimeError):
    """ Custom exception for fatal errors, probably need to quit. """
    def __init__(self, message, error=None):
        super(WebEpicsError, self).__init__(message)
        self.error = error

class WebEpicsWarning(RuntimeWarning):
    """ Custom exception for errors related to file/connectiong handling that are not fatal. """
    def __init__(self, message, error=None):
        super(WebEpicsWarning, self).__init__(message)
        self.error = error
