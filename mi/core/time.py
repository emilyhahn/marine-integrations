#!/usr/bin/env python

"""
@package mi.core.time
@file mi/core/time.py
@author Bill French
@brief Common time functions for drivers
"""
# Needed because we import the time module below.  With out this '.' is search first
# and we import ourselves.
from __future__ import absolute_import

__author__ = 'Bill French'
__license__ = 'Apache 2.0'

from mi.core.log import get_logger ; log = get_logger()

import datetime
import time
import math

def get_timestamp_delayed(format, align='second', offset=0):
    '''
    Return a formatted date string of the current utc time,
    but the string return is delayed until the next second
    transition.

    Formatting:
    http://docs.python.org/library/time.html#time.strftime

    @param format: strftime() format string
    @param align: text string indicating alignment units, 'second' or 'minute'
    @param offset: value to offset the delayed time from to account for the
                   time it takes to set the clock.  This is currently only used
                   for minute alignment, and in this case has units of seconds. 
    @return: formatted date string
    @raise ValueError if format is None
    '''
    if(not format):
        raise ValueError

    result = None
    now = datetime.datetime.utcnow()

    # align to the closest second
    if align == 'second':
        # If we are too close to a second transition then sleep for a bit.
        if(now.microsecond < 100000):
            time.sleep(0.2)
            now = datetime.datetime.utcnow()

        current = datetime.datetime.utcnow()
        while(current.microsecond > now.microsecond):
            current = datetime.datetime.utcnow()
    # align to the closest minute
    elif align == 'minute':
        # make sure the offset is less than 60, since we should not offset more
        # than a minute
        if (offset >= 60):
            raise ValueError('Offset is greater than 60')

        # figure out which second we want to return the time at
        transition_second = math.floor(60 - offset)
        transition_microsecond = 1 - (offset - math.floor(offset))
        if transition_microsecond == 1:
            transition_microsecond = 0

        # if we are close to the transition second, sleep past the transition
        # to go into the next minute
        log.info("Starting second: %s, transition second: %s, transition microsecond %s",
                 now.second, transition_second, transition_microsecond)
        transition_diff = transition_second - now.second
        if((transition_diff < 3 and transition_diff >= 0) or
           transition_diff == 60):
            # get to the next minute transition
            time.sleep(3)
            now = datetime.datetime.utcnow()
 
        # sleep to get close to next minute transition
        sleep_time = transition_second + transition_microsecond - now.second - 1
        if sleep_time <= 0:
            sleep_time += 60
        time.sleep(sleep_time)

        # tight loop until we cross to the transition second
        now = datetime.datetime.utcnow()
        # since 60==0, we have to be handle this specially
        if transition_second == 60:
            if transition_microsecond == 0:
                while(now.second > 0):
                    now = datetime.datetime.utcnow()
            else:
                while(now.second > 0 or
                      (now.second == 0 and now.microsecond < transition_microsecond)):
                    now = datetime.datetime.utcnow()
        else:
            if transition_microsecond == 0:
                while(now.second < transition_second):
                    now = datetime.datetime.utcnow()
            else:
                while(now.second < transition_second or
                      (now.second == transition_second and
                       now.microsecond < transition_microsecond)):
                    now = datetime.datetime.utcnow()
    else:
        # only 'second' and 'minute' are supported
        raise ValueError('Unsupported align value %s ' % align)

    return time.strftime(format, time.gmtime())


def get_timestamp(format):
    '''
    Return a formatted date string of the current utc time.

    Formatting:
    http://docs.python.org/library/time.html#time.strftime

    @param format: strftime() format string
    @return: formatted date string
    @raise ValueError if format is None
    '''
    if(not format):
        raise ValueError

    return time.strftime(format, time.gmtime())
