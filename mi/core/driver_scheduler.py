#!/usr/bin/env python

"""
@package mi.core.driver_scheduler Event Scheduler used in drivers
@file mi/core/driver_scheduler.py
@author Bill French
@brief Provides task/event scheduling for drivers
uses the PolledScheduler and provides a common, simplified interface
for instrument and platform drivers.

Configuration Dict:




"""

__author__ = 'Bill French'
__license__ = 'Apache 2.0'

import inspect

from mi.core.log import get_logger; log = get_logger()

from mi.core.common import BaseEnum
from mi.core.scheduler import PolledScheduler
from mi.core.exceptions import SchedulerException

class TriggerType(BaseEnum):
    ABSOLUTE = 'absolute'
    INTERVAL = 'interval'
    CRON = 'cron'
    POLLED_INTERVAL = 'polled'

class DriverSchedulerConfigKey(BaseEnum):
    # Common Config Constants
    TRIGGER = 'trigger'
    CALLBACK = 'callback'

    ###
    # Trigger Specific Constants
    ###
    TRIGGER_TYPE = 'type'

    # Absolute Date
    DATE = 'date'

    # Polled Interval
    MINIMAL_INTERVAL = 'minimum_interval'
    MAXIMUM_INTERVAL = 'maximum_interval'

    # Interval and Polled Interval
    WEEKS = 'weeks'
    DAYS = 'days'
    HOURS = 'hours'
    MINUTES = 'minutes'
    SECONDS = 'seconds'

    # Cron
    YEAR = 'year'
    MONTH = 'month'
    DAY = 'day'
    WEEK = 'week'
    DAY_OF_WEEK = 'day_of_week'
    HOUR = 'hour'
    MINUTE = 'minute'
    SECOND = 'second'


class DriverScheduler(object):
    """
    Class to facilitate event scheduling in drivers.
    jobs.
    """

    def __init__(self, config = None):
        """
        config structure:
        {
            test_name: {
                trigger: {}
                callback: some_function
            }
        }
        @param config: job configuration structure.
        """
        self._scheduler = PolledScheduler()
        if(config):
            self.add_config(config)

    def add_config(self, config):
        """
        Add new jobs to the scheduler using the passed in config
        config structure:
        {
            test_name: {
                trigger: {}
                callback: some_function
            }
        }
        @param config: job configuration structure.
        @raise SchedulerException if we fail to add the job
        """
        if(not isinstance(config, dict)):
            raise SchedulerException("scheduler config not a dict")

        if(len(config.keys()) == 0):
            raise SchedulerException("scheduler config empty")

        for (name, config) in config.items():
            try:
                self._add_job(name, config)
            except ValueError as e:
                raise SchedulerException("failed to schedule job: %s" % e)
            except TypeError as e:
                raise SchedulerException("failed to schedule job: %s" % e)

        if(not self._scheduler.running):
            self._scheduler.start()

    def _add_job(self, name, config):
        """
        Add a new job to the scheduler based on the trigger configuration
        @param name: name of the job
        @param config: job configuration
        @raise SchedulerError if we fail to add the job
        """
        log.debug(" Config name: %s value: %s" % (name, config))

        if(config == None):
            raise SchedulerException("job config empty")

        if(not isinstance(config, dict)):
            raise SchedulerException("job config not a dict")

        trigger = self._get_trigger_from_config(config)

        trigger_type = trigger.get(DriverSchedulerConfigKey.TRIGGER_TYPE)
        if(trigger_type == None):
            raise SchedulerException("trigger type missing")

        if(trigger_type == TriggerType.ABSOLUTE):
            self._add_job_absolute(name, config)
        elif(trigger_type == TriggerType.CRON):
            self._add_job_cron(name, config)
        elif(trigger_type == TriggerType.INTERVAL):
            self._add_job_interval(name, config)
        elif(trigger_type == TriggerType.POLLED_INTERVAL):
            self._add_job_polled_interval(name, config)
        else:
            raise SchedulerException("unknown trigger type '%s'" % trigger_type)

    def _get_trigger_from_config(self, config):
        trigger = config.get(DriverSchedulerConfigKey.TRIGGER)
        if(trigger == None):
            raise SchedulerException("trigger definition missing")
        if(not isinstance(trigger, dict)):
            raise SchedulerException("config missing trigger definition")

        return trigger

    def _get_callback_from_config(self, config):
        callback = config.get(DriverSchedulerConfigKey.CALLBACK)
        if(callback == None):
            raise SchedulerException("callback definition missing")
        if(not inspect.ismethod(callback)):
            raise SchedulerException("callback incorrect type: '%s'" % type(callback))

        return callback

    def _add_job_absolute(self, name, config):
        """
        Add a new job to the scheduler based on the trigger configuration
        @param name: name of the job
        @param config: job configuration
        @raise SchedulerError if we fail to add the job
        """
        if(not isinstance(config, dict)):
            raise SchedulerException("config not a dict")

        callback = self._get_callback_from_config(config)
        trigger = self._get_trigger_from_config(config)

        dt = trigger.get(DriverSchedulerConfigKey.DATE)
        if(dt == None):
            raise SchedulerException("trigger missing parameter: %s" % DriverSchedulerConfigKey.DATE)

        self._scheduler.add_date_job(callback, dt)

    def _add_job_cron(self, name, config):
        """
        Add a new job to the scheduler based on the trigger configuration
        @param name: name of the job
        @param config: job configuration
        @raise SchedulerError if we fail to add the job
        """
        if(not isinstance(config, dict)):
            raise SchedulerException("config not a dict")

        callback = self._get_callback_from_config(config)
        trigger = self._get_trigger_from_config(config)

        year = trigger.get(DriverSchedulerConfigKey.YEAR)
        month = trigger.get(DriverSchedulerConfigKey.MONTH)
        day = trigger.get(DriverSchedulerConfigKey.DAY)
        week = trigger.get(DriverSchedulerConfigKey.WEEK)
        day_of_week = trigger.get(DriverSchedulerConfigKey.DAY_OF_WEEK)
        hour = trigger.get(DriverSchedulerConfigKey.HOUR)
        minute = trigger.get(DriverSchedulerConfigKey.MINUTE)
        second = trigger.get(DriverSchedulerConfigKey.SECOND)

        if(year==None and month==None and day==None and week==None and
           day_of_week==None and hour==None and minute==None and second==None):
            raise SchedulerException("at least one cron parameter required!")

        self._scheduler.add_cron_job(callback, year=year, month=month, day=day, week=week,
                                     day_of_week=day_of_week, hour=hour, minute=minute, second=second)

    def _add_job_interval(self, name, config):
        """
        Add a new job to the scheduler based on the trigger configuration
        @param name: name of the job
        @param config: job configuration
        @raise SchedulerError if we fail to add the job
        """
        if(not isinstance(config, dict)):
            raise SchedulerException("config not a dict")

        callback = self._get_callback_from_config(config)
        trigger = self._get_trigger_from_config(config)

        weeks = trigger.get(DriverSchedulerConfigKey.WEEKS, 0)
        days = trigger.get(DriverSchedulerConfigKey.DAYS, 0)
        hours = trigger.get(DriverSchedulerConfigKey.HOURS, 0)
        minutes = trigger.get(DriverSchedulerConfigKey.MINUTES, 0)
        seconds = trigger.get(DriverSchedulerConfigKey.SECONDS, 0)

        if(not (weeks or days or hours or minutes or seconds)):
            raise SchedulerException("at least interval parameter required!")

        self._scheduler.add_interval_job(callback, weeks=weeks, days=days, hours=hours,
                                                   minutes=minutes, seconds=seconds)

    def _add_job_polled_interval(self, name, config):
        """
        Add a new job to the scheduler based on the trigger configuration
        @param name: name of the job
        @param config: job configuration
        @raise SchedulerError if we fail to add the job
        """
        if(not isinstance(config, dict)):
            raise SchedulerException("config not a dict")

        callback = self._get_callback_from_config(config)
        trigger = self._get_trigger_from_config(config)

        min_interval = trigger.get(DriverSchedulerConfigKey.MINIMAL_INTERVAL)
        max_interval = trigger.get(DriverSchedulerConfigKey.MAXIMUM_INTERVAL)

        if(min_interval == None):
            raise SchedulerException("%s missing from trigger configuration" % DriverSchedulerConfigKey.MINIMAL_INTERVAL)
        if(not isinstance(min_interval, dict)):
            raise SchedulerException("%s trigger configuration not a dict" % DriverSchedulerConfigKey.MINIMAL_INTERVAL)

        min_weeks = min_interval.get(DriverSchedulerConfigKey.WEEKS, 0)
        min_days = min_interval.get(DriverSchedulerConfigKey.DAYS, 0)
        min_hours = min_interval.get(DriverSchedulerConfigKey.HOURS, 0)
        min_minutes = min_interval.get(DriverSchedulerConfigKey.MINUTES, 0)
        min_seconds = min_interval.get(DriverSchedulerConfigKey.SECONDS, 0)

        if(not (min_weeks or min_days or min_hours or min_minutes or min_seconds)):
            raise SchedulerException("at least interval parameter required!")

        min_interval_obj = self._scheduler.interval(min_weeks, min_days, min_hours, min_minutes, min_seconds)

        max_interval_obj = None
        if(max_interval != None):
            if(not isinstance(max_interval, dict)):
                raise SchedulerException("%s trigger configuration not a dict" % DriverSchedulerConfigKey.MINIMAL_INTERVAL)

            max_weeks = max_interval.get(DriverSchedulerConfigKey.WEEKS, 0)
            max_days = max_interval.get(DriverSchedulerConfigKey.DAYS, 0)
            max_hours = max_interval.get(DriverSchedulerConfigKey.HOURS, 0)
            max_minutes = max_interval.get(DriverSchedulerConfigKey.MINUTES, 0)
            max_seconds = max_interval.get(DriverSchedulerConfigKey.SECONDS, 0)

            if(max_weeks or max_days or max_hours or max_minutes or max_seconds):
                max_interval_obj = self._scheduler.interval(max_weeks, max_days, max_hours, max_minutes, max_seconds)

        self._scheduler.add_polled_job(callback, name, min_interval_obj, max_interval_obj)



