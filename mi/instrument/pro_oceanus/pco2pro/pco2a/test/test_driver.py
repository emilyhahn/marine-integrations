"""
@package mi.instrument.pro_oceanus.pco2pro.pco2a.test.test_driver
@file marine-integrations/mi/instrument/pro_oceanus/pco2pro/pco2a/driver.py
@author E. Hahn
@brief Test cases for ooicore driver

USAGE:
 Make tests verbose and provide stdout
   * From the IDK
       $ bin/test_driver
       $ bin/test_driver -u [-t testname]
       $ bin/test_driver -i [-t testname]
       $ bin/test_driver -q [-t testname]
"""

__author__ = 'E. Hahn'
__license__ = 'Apache 2.0'

import time
import unittest
import re
from time import strftime, localtime

from nose.plugins.attrib import attr
from mock import Mock

from mi.core.log import get_logger ; log = get_logger()

# MI imports.
from mi.idk.unit_test import InstrumentDriverTestCase
from mi.idk.unit_test import InstrumentDriverUnitTestCase
from mi.idk.unit_test import InstrumentDriverIntegrationTestCase
from mi.idk.unit_test import InstrumentDriverQualificationTestCase
from mi.idk.unit_test import ParameterTestConfigKey
from mi.idk.unit_test import DriverTestMixin
from mi.idk.util import convert_enum_to_dict

from interface.objects import AgentCommand

from mi.core.instrument.logger_client import LoggerClient

from mi.core.instrument.chunker import StringChunker
from mi.core.instrument.instrument_driver import DriverAsyncEvent
from mi.core.instrument.instrument_driver import DriverConnectionState
from mi.core.instrument.instrument_driver import DriverProtocolState

from mi.core.instrument.data_particle import RawDataParticle

from mi.core.exceptions import InstrumentException
from mi.core.exceptions import InstrumentProtocolException
from mi.core.exceptions import InstrumentParameterException
from mi.core.exceptions import InstrumentCommandException

from ion.agents.instrument.instrument_agent import InstrumentAgentState
from ion.agents.instrument.direct_access.direct_access_server import DirectAccessTypes

from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Pco2aInstrumentDriver
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import DataParticleType
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import ProtocolState
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import ProtocolEvent
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Capability
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Parameter
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Protocol
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Prompt
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import NEWLINE
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import AutoSampleMode
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import AUTO_SAMPLE_MENU_OPTS
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import AUTO_SAMPLE_STR
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Command
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import SubMenu
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import MENU
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import COMMAND_CHAR
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Pco2aAirSampleDataParticleKey
from mi.instrument.pro_oceanus.pco2pro.pco2a.driver import Pco2aWaterSampleDataParticleKey

# SAMPLE DATA FOR TESTING
from mi.instrument.pro_oceanus.pco2pro.pco2a.test.sample_data import *

###
#   Driver parameters for the tests
###
InstrumentDriverTestCase.initialize(
    driver_module='mi.instrument.pro_oceanus.pco2pro.pco2a.driver',
    driver_class="Pco2aInstrumentDriver",

    instrument_agent_resource_id = 'HYBCAE',
    instrument_agent_name = 'pro_oceanus_pco2pro_pco2a',
    instrument_agent_packet_config = DataParticleType(),

    driver_startup_config = {}
)

#################################### RULES ####################################
#                                                                             #
# Common capabilities in the base class                                       #
#                                                                             #
# Instrument specific stuff in the derived class                              #
#                                                                             #
# Generator spits out either stubs or comments describing test this here,     #
# test that there.                                                            #
#                                                                             #
# Qualification tests are driven through the instrument_agent                 #
#                                                                             #
###############################################################################

###
#   Driver constant definitions
###

###############################################################################
#                           DRIVER TEST MIXIN        		              #
#     Defines a set of constants and assert methods used for data particle    #
#     verification 							      #
#                                                                             #
#  In python mixin classes are classes designed such that they wouldn't be    #
#  able to stand on their own, but are inherited by other classes generally   #
#  using multiple inheritance.                                                #
#                                                                             #
# This class defines a configuration structure for testing and common assert  #
# methods for validating data particles.				      #
###############################################################################
class Pco2aTestMixin(DriverTestMixin):
    '''
    Mixin class used for storing data particle constance and common data assertion methods.
    '''
    # Create some short names for the parameter test config
    TYPE      = ParameterTestConfigKey.TYPE
    READONLY  = ParameterTestConfigKey.READONLY
    STARTUP   = ParameterTestConfigKey.STARTUP
    DA        = ParameterTestConfigKey.DIRECT_ACCESS
    VALUE     = ParameterTestConfigKey.VALUE
    REQUIRED  = ParameterTestConfigKey.REQUIRED
    DEFAULT   = ParameterTestConfigKey.DEFAULT
    STATES    = ParameterTestConfigKey.STATES

    ###
    #  Parameter and Type Definitions
    ###
    _driver_parameters = {
        # Parameters defined in the IOS
        Parameter.NUMBER_SAMPLES : {TYPE: int, READONLY: False,
                                    DA: False, STARTUP: True,
                                    DEFAULT: 5, VALUE: 5},
        Parameter.MENU_WAIT_TIME : {TYPE: int, READONLY: False, DA: False,
                                    STARTUP: True, DEFAULT: 20, VALUE: 20},
        Parameter.AUTO_SAMPLE_MODE : {TYPE: str, READONLY: False,
                                      DA: False, STARTUP: True,
                                      DEFAULT: AutoSampleMode.ONE_HR_SAMPLE,
                                      VALUE: AutoSampleMode.ONE_HR_SAMPLE},
        Parameter.ATMOSPHERE_MODE : {TYPE: int, READONLY: False,
                                     DA: False, STARTUP: True,
                                     DEFAULT: 2, VALUE: 2}
        }
    _driver_capabilities = {
        # capabilities defined in the IOS
        Capability.START_AUTOSAMPLE : {STATES: [ProtocolState.COMMAND,
                                                ProtocolState.AUTOSAMPLE]},
        Capability.STOP_AUTOSAMPLE : {STATES: [ProtocolState.COMMAND,
                                               ProtocolState.AUTOSAMPLE]},
        Capability.CLOCK_SYNC : {STATES: [ProtocolState.COMMAND]},
    }
    
    _air_sample_params = {
        # air sample
        Pco2aAirSampleDataParticleKey.DATE_TIME_STRING :
            {TYPE: unicode, VALUE: '2013/05/17 19:29:58', REQUIRED: True},
        Pco2aAirSampleDataParticleKey.BEGIN_MEASUREMENT :
            {TYPE: unicode, VALUE: 'M', REQUIRED: True},
        Pco2aAirSampleDataParticleKey.ZERO_A2D :
            {TYPE: int, VALUE: 46238, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.CURRENT_A2D :
            {TYPE: int, VALUE: 43364, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.MEASURED_AIR_CO2 :
            {TYPE: float, VALUE: 419.9, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.AVG_IRG_TEMPERATURE :
            {TYPE: float, VALUE: 42.9, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.HUMIDITY :
            {TYPE: float, VALUE: 13.299, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.HUMIDITY_TEMPERATURE :
            {TYPE: float, VALUE: 37.240, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.GAS_STREAM_PRESSURE :
            {TYPE: int, VALUE: 1036, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.IRGA_DETECTOR_TEMPERATURE :
            {TYPE: float, VALUE: 42.5, REQUIRED: True},
        Pco2aAirSampleDataParticleKey.IRGA_SOURCE_TEMPERATURE :
            {TYPE: float, VALUE: 43.4, REQUIRED: True}
    }
    
    _water_sample_params = {
        # water sample
        Pco2aWaterSampleDataParticleKey.DATE_TIME_STRING :
            {TYPE: unicode, VALUE: '2013/05/17 18:59:43', REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.BEGIN_MEASUREMENT :
            {TYPE: unicode, VALUE: 'M', REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.ZERO_A2D :
            {TYPE: int, VALUE: 46238, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.CURRENT_A2D :
            {TYPE: int, VALUE: 42913, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.MEASURED_WATER_CO2 :
            {TYPE: float, VALUE: 511.9, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.AVG_IRG_TEMPERATURE :
            {TYPE: float, VALUE: 42.9, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.HUMIDITY :
            {TYPE: float, VALUE: 14.129, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.HUMIDITY_TEMPERATURE :
            {TYPE: float, VALUE: 33.920, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.GAS_STREAM_PRESSURE :
            {TYPE: int, VALUE: 1018, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.IRGA_DETECTOR_TEMPERATURE :
            {TYPE: float, VALUE: 42.4, REQUIRED: True},
        Pco2aWaterSampleDataParticleKey.IRGA_SOURCE_TEMPERATURE :
            {TYPE: float, VALUE: 43.3, REQUIRED: True}
    }
    
    ###
    #   Driver Parameter Methods
    ###
    def assert_driver_parameters(self, current_parameters, verify_values=False):
        """
        Verify that all driver parameters are correct and potentially verify
        values.
        @param current_parameters: driver parameters read from the driver
        instance
        """       
        self.assert_parameters(current_parameters, self._driver_parameters)
        
    def assert_particle_air_sample(self, data_particle, verify_values = False):
        '''
        Verify air_sample particle
        @param data_particle:  Pco2aAirSampleDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(Pco2aAirSampleDataParticleKey,
                                       self._air_sample_params)
        self.assert_data_particle_header(data_particle,
                                         DataParticleType.PCO2A_AIR_SAMPLES)
        self.assert_data_particle_parameters(data_particle,
                                             self._air_sample_params,
                                             verify_values)

    def assert_particle_water_sample(self, data_particle,
                                     verify_values = False):
        '''
        Verify water_sample particle
        @param data_particle:  Pco2aWaterSampleRefOscDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(Pco2aWaterSampleDataParticleKey,
                                       self._water_sample_params)
        self.assert_data_particle_header(data_particle,
                                         DataParticleType.PCO2A_WATER_SAMPLES)
        self.assert_data_particle_parameters(data_particle,
                                             self._water_sample_params,
                                             verify_values)
    

###############################################################################
#                                UNIT TESTS                                   #
#         Unit tests test the method calls and parameters using Mock.         #
#                                                                             #
#   These tests are especially useful for testing parsers and other data      #
#   handling.  The tests generally focus on small segments of code, like a    #
#   single function call, but more complex code using Mock objects.  However  #
#   if you find yourself mocking too much maybe it is better as an            #
#   integration test.                                                         #
#                                                                             #
#   Unit tests do not start up external processes like the port agent or      #
#   driver process.                                                           #
###############################################################################
@attr('UNIT', group='mi')
class Pco2aUnitTest(InstrumentDriverUnitTestCase, Pco2aTestMixin):
    def setUp(self):
        InstrumentDriverUnitTestCase.setUp(self)

    def test_driver_enums(self):
        """
        Verify that all driver enumeration has no duplicate values that might cause confusion.  Also
        do a little extra validation for the Capabilites
        """
        self.assert_enum_has_no_duplicates(DataParticleType())
        self.assert_enum_has_no_duplicates(ProtocolState())
        self.assert_enum_has_no_duplicates(ProtocolEvent())
        self.assert_enum_has_no_duplicates(AutoSampleMode())
        self.assert_enum_has_no_duplicates(Parameter())
        self.assert_enum_has_no_duplicates(Command())
        self.assert_enum_has_no_duplicates(SubMenu())
        self.assert_enum_has_no_duplicates(Prompt())

        # Test capabilites for duplicates, them verify that capabilities is a subset of proto events
        self.assert_enum_has_no_duplicates(Capability())
        self.assert_enum_complete(Capability(), ProtocolEvent())
        
        cmd = convert_enum_to_dict(Command)
        cmd_char = COMMAND_CHAR
        self.assert_set_complete(cmd_char, cmd)
        
        asm = convert_enum_to_dict(AutoSampleMode)
        asm_menu_char = AUTO_SAMPLE_MENU_OPTS
        self.assert_set_complete(asm_menu_char, asm)
        
    def test_driver_schema(self):
        """
        get the driver schema and verify it is configured properly
        """
        driver = Pco2aInstrumentDriver(self._got_data_event_callback)
        self.assert_driver_schema(driver, self._driver_parameters, self._driver_capabilities)

    def test_chunker(self):
        """
        Test the chunker and verify the particles created.
        """
        chunker = StringChunker(Protocol.sieve_function)
        
        self.assert_chunker_sample(chunker, SAMPLE_AIR)
        self.assert_chunker_sample_with_noise(chunker, SAMPLE_AIR)
        self.assert_chunker_fragmented_sample(chunker, SAMPLE_AIR, 32)
        self.assert_chunker_combined_sample(chunker, SAMPLE_AIR)
        
        self.assert_chunker_sample(chunker, SAMPLE_WATER)
        self.assert_chunker_sample_with_noise(chunker, SAMPLE_WATER)
        self.assert_chunker_fragmented_sample(chunker, SAMPLE_WATER, 32)
        self.assert_chunker_combined_sample(chunker, SAMPLE_WATER)

    def test_got_data(self):
        """
        Verify sample data passed through the got data method produces the correct data particles
        """
        # Create and initialize the instrument driver with a mock port agent
        driver = Pco2aInstrumentDriver(self._got_data_event_callback)
        self.assert_initialize_driver(driver)
        
        self.assert_raw_particle_published(driver, True)
        
        # Start validating data particles
        self.assert_particle_published(driver, SAMPLE_AIR, self.assert_particle_air_sample, True)
        self.assert_particle_published(driver, SAMPLE_WATER, self.assert_particle_water_sample, True)

    def test_protocol_filter_capabilities(self):
        """
        This tests driver filter_capabilities.
        Iterate through available capabilities, and verify that they can pass successfully through the filter.
        Test silly made up capabilities to verify they are blocked by filter.
        """
        mock_callback = Mock()
        protocol = Protocol(MENU, Prompt, NEWLINE, mock_callback)
        driver_capabilities = Capability().list()
        test_capabilities = Capability().list()

        # Add a bogus capability that will be filtered out.
        test_capabilities.append("BOGUS_CAPABILITY")

        # Verify "BOGUS_CAPABILITY was filtered out
        self.assertEquals(sorted(driver_capabilities),
                          sorted(protocol._filter_capabilities(test_capabilities)))
        
    def test_capabilities(self):
        """
        Verify the FSM reports capabilities as expected.  All states defined in this dict must
        also be defined in the protocol FSM.
        """
        capabilities = {
            ProtocolState.UNKNOWN: ['DRIVER_EVENT_DISCOVER'],
            ProtocolState.DISCOVERY: ['DRIVER_EVENT_DISCOVER',
                                      'DRIVER_EVENT_START_DIRECT',
                                      'PROTOCOL_EVENT_DISCOVER_COMMAND',
                                      'PROTOCOL_EVENT_DISCOVER_AUTOSAMPLE'],
            ProtocolState.COMMAND: ['DRIVER_EVENT_CLOCK_SYNC',
                                    'DRIVER_EVENT_GET',
                                    'DRIVER_EVENT_SET',
                                    'DRIVER_EVENT_START_AUTOSAMPLE',
                                    'DRIVER_EVENT_START_DIRECT',
                                    'PROTOCOL_EVENT_DISCOVER_AUTOSAMPLE'],
            ProtocolState.AUTOSAMPLE: ['DRIVER_EVENT_STOP_AUTOSAMPLE'],
            ProtocolState.WAIT_FOR_COMMAND: ['PROTOCOL_EVENT_DISCOVER_COMMAND'],
            ProtocolState.DIRECT_ACCESS: ['DRIVER_EVENT_STOP_DIRECT',
                                          'EXECUTE_DIRECT'], 
        }

        driver = Pco2aInstrumentDriver(self._got_data_event_callback)
        self.assert_capabilities(driver, capabilities)
        
    def test_to_autosample(self):
        """ Test to autosample conversion. """
        self.assertEquals(AutoSampleMode.DAILY_SAMPLE, Protocol._to_autosample('Daily'))
        self.assertEquals(AutoSampleMode.HALF_HR_SAMPLE, Protocol._to_autosample('30 Minute'))
        self.assertEquals(AutoSampleMode.CONTINUOUS_SAMPLE, Protocol._to_autosample('Continuous'))
        self.assertRaises(InstrumentParameterException,
                          Protocol._to_autosample, 'Bad String')
        
    def test_from_autosample(self):
        """ Test from autosample conversion. """
        self.assertEquals('0', Protocol._from_autosample(AutoSampleMode.NO_AUTO_SAMPLE))
        self.assertEquals('2', Protocol._from_autosample(AutoSampleMode.ONE_HR_SAMPLE))
        self.assertEquals('4', Protocol._from_autosample(AutoSampleMode.SIX_HR_SAMPLE))
        self.assertRaises(InstrumentParameterException,
                          Protocol._from_autosample, 10)
        self.assertRaises(InstrumentParameterException,
                          Protocol._from_autosample, 'Bad String')
        
    def test_to_menu_wait_time(self):
        """
        Test that the conversion from wait time number to the value entered
        at the menu is correct
        """
        self.assertEquals('015', Protocol._to_menu_wait_time(15))
        self.assertEquals('005', Protocol._to_menu_wait_time(5))
        self.assertEquals('100', Protocol._to_menu_wait_time(100))
        self.assertRaises(InstrumentParameterException, Protocol._to_menu_wait_time, 0)
        self.assertRaises(InstrumentParameterException, Protocol._to_menu_wait_time, 'A')
        self.assertRaises(InstrumentParameterException, Protocol._to_menu_wait_time, 1500)
                   

###############################################################################
#                            INTEGRATION TESTS                                #
#     Integration test test the direct driver / instrument interaction        #
#     but making direct calls via zeromq.                                     #
#     - Common Integration tests test the driver through the instrument agent #
#     and common for all drivers (minimum requirement for ION ingestion)      #
###############################################################################
@attr('INT', group='mi')
class Pco2aIntegrationTest(InstrumentDriverIntegrationTestCase, Pco2aTestMixin):
    def setUp(self):
        InstrumentDriverIntegrationTestCase.setUp(self)
                 
    def test_startup_params(self):
        """
        Verify that startup parameters are applied correctly. 
        """
        
        # Explicitly verify these values after discover.  They should match
        # what the startup values should be
        get_values = {
            Parameter.NUMBER_SAMPLES: 5,
            Parameter.MENU_WAIT_TIME: 20,
            Parameter.AUTO_SAMPLE_MODE: AutoSampleMode.ONE_HR_SAMPLE,
            Parameter.ATMOSPHERE_MODE: 2
        }

        # Change the values of these parameters to something before the
        # driver is reinitalized.  They should be blown away on reinit.
        new_values = {
            Parameter.NUMBER_SAMPLES: 3,
            Parameter.MENU_WAIT_TIME: 15,
            Parameter.AUTO_SAMPLE_MODE: AutoSampleMode.HALF_HR_SAMPLE,
            Parameter.ATMOSPHERE_MODE: 1
        }

        self.assert_initialize_driver()
        # make sure startup parameters are set by setting new values, then
        # re-initializing and checking for init values
        self.assert_startup_parameters(self.assert_driver_parameters, new_values, get_values)
        
    def test_set(self):
        """
        Test all set commands. Verify all exception cases.
        """
        
        self.assert_initialize_driver()

        #   Instrument Parameters

        # Number of Samples.  integer 1 - 9
        self.assert_set(Parameter.NUMBER_SAMPLES, 1)
        self.assert_set(Parameter.NUMBER_SAMPLES, 9)
        self.assert_set_exception(Parameter.NUMBER_SAMPLES, 10)
        self.assert_set_exception(Parameter.NUMBER_SAMPLES, 0)
        self.assert_set_exception(Parameter.NUMBER_SAMPLES, -1)
        self.assert_set_exception(Parameter.NUMBER_SAMPLES, 0.2)
        self.assert_set_exception(Parameter.NUMBER_SAMPLES, "1")
        
        # Menu wait time. integer 1 - 560
        self.assert_set(Parameter.MENU_WAIT_TIME, 560)
        self.assert_set(Parameter.MENU_WAIT_TIME, 1)
        # need to actually set the menu wait time to greater than 1
        # in order for code to be able to escape, set back to default
        self.assert_set(Parameter.MENU_WAIT_TIME, 20)
        self.assert_set_exception(Parameter.MENU_WAIT_TIME, 570)
        self.assert_set_exception(Parameter.MENU_WAIT_TIME, 0)
        self.assert_set_exception(Parameter.MENU_WAIT_TIME, -1)
        self.assert_set_exception(Parameter.MENU_WAIT_TIME, 0.2)
        self.assert_set_exception(Parameter.MENU_WAIT_TIME, "1")
        
        # Auto Sample Mode. AutoSampleMode enum
        self.assert_set(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.DAILY_SAMPLE)
        self.assert_set(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.CONTINUOUS_SAMPLE)
        self.assert_set_exception(Parameter.AUTO_SAMPLE_MODE, 0)
        self.assert_set_exception(Parameter.AUTO_SAMPLE_MODE, -1)
        self.assert_set_exception(Parameter.AUTO_SAMPLE_MODE, 0.2)
        self.assert_set_exception(Parameter.AUTO_SAMPLE_MODE, "1")
        
        # Atmosphere Mode. integer 0-2
        self.assert_set(Parameter.ATMOSPHERE_MODE, 0)
        self.assert_set(Parameter.ATMOSPHERE_MODE, 2)
        self.assert_set_exception(Parameter.ATMOSPHERE_MODE, -1)
        self.assert_set_exception(Parameter.ATMOSPHERE_MODE, 3)
        self.assert_set_exception(Parameter.ATMOSPHERE_MODE, "1")
     
    def test_commands_and_autosample(self):
        """
        Run instrument commands from both command and auto sample mode, and
        verify that we can enter streaming and that all particles are produced
        properly.  Do this all in one test because it takes a half hour once you
        go into auto sample mode.

        Because we have to test for two different data particles we can't use
        the common assert_sample_autosample method
        """
        
        self.assert_initialize_driver()
        
        # make sure we are at the shortest auto sampling rate (excluding continuous)
        self.assert_set(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)

        # First test in command mode
        self.assert_driver_command(ProtocolEvent.CLOCK_SYNC)
        
        # Test a bad command in command mode
        self.assert_driver_command_exception('ima_bad_command',
                                             exception_class=InstrumentCommandException)

        # Put us in streaming
        # delay of 20 to make sure we actually enter auto sample and don't escape again
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE,
                                   state=ProtocolState.AUTOSAMPLE, delay=20)
        
        # can't set clock in auto sample mode, should cause exception
        self.assert_driver_command_exception(ProtocolEvent.CLOCK_SYNC,
                                             exception_class=InstrumentCommandException)

        # Test a bad command in autosample mode
        self.assert_driver_command_exception('ima_bad_command',
                                             exception_class=InstrumentCommandException)

        # wait for air and water samples to arrive (need to wait an hour to account for 22 min
        # startup, plus half hour before sample on minute 0 or 30)
        self.assert_async_particle_generation(DataParticleType.PCO2A_AIR_SAMPLES,
                                              self.assert_particle_air_sample, timeout=3600)
        # both particles are taken at the same time, so there shouldn't be any delay between air
        # and water samples, but just give it a few minutes in case
        self.assert_async_particle_generation(DataParticleType.PCO2A_WATER_SAMPLES,
                                              self.assert_particle_water_sample, timeout=360)
        
        # try to get out of auto sample mode ()
        self.assert_driver_command(ProtocolEvent.STOP_AUTOSAMPLE, delay=1)
        
        # Test a bad command in wait for command mode
        self.assert_driver_command_exception('ima_bad_command',
                                             exception_class=InstrumentCommandException)
        
        # loop waiting for the instrument to come back from auto sample
        self.assert_state_change(ProtocolState.COMMAND, timeout=1800, sleep_time=60)
        
    def test_discover_from_autosample_pre_sample(self):
        """
        Test that we can discover what mode we are in if the driver
         starts and the instrument is put into auto sampling but hasn't actually
         sampled yet.
        """
        
        self.assert_initialize_driver()
        
        # make sure we are at the shortest auto sampling rate (excluding continuous)
        self.assert_set(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        # Put us in streaming
        # delay of 20 to make sure we actually enter auto sample and don't escape again
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE,
                                   state=ProtocolState.AUTOSAMPLE, delay=20)
        
        # disconnect the driver
        reply = self.driver_client.cmd_dvr('disconnect')
        self.assertEqual(reply, None)

        self.assert_current_state(DriverConnectionState.DISCONNECTED)
        
        # Initialize the driver and transition to unconfigured.
        reply = self.driver_client.cmd_dvr('initialize')
    
        # Test the driver is in state unconfigured.
        state = self.driver_client.cmd_dvr('get_resource_state')
        self.assertEqual(state, DriverConnectionState.UNCONFIGURED)
        
        # Then reconnect the driver 
        self.assert_initialize_driver()
        
    def test_discover_from_autosample_post_sample(self):
        """
        Test that we can discover what mode we are in if the driver starts and the 
         instrument is auto sampling and has taken a sample.  In the post sample test, the
         driver must initially realize that it is in autosample mode and exit out of it
         into command mode.  In the pre_sample test, the driver gets the text to exit
         autosample mode while it is still discovering. 
        """
    
        self.assert_initialize_driver()
        
        # make sure we are at the shortest auto sampling rate (excluding continuous)
        self.assert_set(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        # Put us in streaming
        # delay of 20 to make sure we actually enter auto sample and don't escape again
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE,
                                   state=ProtocolState.AUTOSAMPLE, delay=20)
        
        # wait for air and water samples to arrive (need to wait an hour to account for 22 min
        # startup, plus half hour before sample on minute 0 or 30)
        self.assert_async_particle_generation(DataParticleType.PCO2A_AIR_SAMPLES,
                                              self.assert_particle_air_sample, timeout=3600)
        # both particles are taken at the same time, so there shouldn't be any delay between air
        # and water samples, but just give it a few minutes in case
        self.assert_async_particle_generation(DataParticleType.PCO2A_WATER_SAMPLES,
                                              self.assert_particle_water_sample, timeout=360)
        
        # disconnect the driver
        reply = self.driver_client.cmd_dvr('disconnect')
        self.assertEqual(reply, None)

        self.assert_current_state(DriverConnectionState.DISCONNECTED)
        
        # Initialize the driver and transition to unconfigured.
        reply = self.driver_client.cmd_dvr('initialize')
    
        # Test the driver is in state unconfigured.
        state = self.driver_client.cmd_dvr('get_resource_state')
        self.assertEqual(state, DriverConnectionState.UNCONFIGURED)
        
        # Then reconnect the driver 
        self.assert_initialize_driver()
                 
    def assert_initialize_driver(self):
        """
        Walk an uninitialized driver through it's initialize process.  Verify the final
        state is command mode.  
        """
        # Test the driver is in state unconfigured.
        self.assert_current_state(DriverConnectionState.UNCONFIGURED)

        # Configure driver for comms and transition to disconnected.
        reply = self.driver_client.cmd_dvr('configure', self.port_agent_comm_config())

        # Test the driver is configured for comms.
        self.assert_current_state(DriverConnectionState.DISCONNECTED)

        # Configure driver for comms and transition to disconnected.
        reply = self.driver_client.cmd_dvr('connect')

        # Test the driver is in unknown state.
        self.assert_current_state(DriverProtocolState.UNKNOWN)
        
        # Configure driver for comms and transition to disconnected./
        reply = self.driver_client.cmd_dvr('discover_state')
        
        # At this point state should always be either autosample or command,
        # since that is what the discover handler is waiting for
        state = self.driver_client.cmd_dvr('get_resource_state')
        
        # If we are in auto sample mode, try to break out of it
        # (this may take a while, up to your sampling time)
        if (state == ProtocolState.AUTOSAMPLE):
            log.debug("Stopping auto sample to go to command")
            reply = self.driver_client.cmd_dvr('execute_resource', ProtocolEvent.STOP_AUTOSAMPLE)
        
        # loop waiting until we discover command mode
        self.assert_state_change(ProtocolState.COMMAND, timeout=3600, sleep_time=30)
        
        # Just make really sure the driver is in command mode.
        self.assert_current_state(ProtocolState.COMMAND)      



###############################################################################
#                            QUALIFICATION TESTS                              #
# Device specific qualification tests are for doing final testing of ion      #
# integration.  The generally aren't used for instrument debugging and should #
# be tackled after all unit and integration tests are complete                #
###############################################################################
@attr('QUAL', group='mi')
class Pco2aQualificationTest(InstrumentDriverQualificationTestCase):
    def setUp(self):
        InstrumentDriverQualificationTestCase.setUp(self)

    def test_direct_access_telnet_mode(self):
        """
        @brief This test manually tests that the Instrument Driver properly supports direct access to the physical instrument. (telnet mode)
        """
        self.assert_direct_access_start_telnet()
        self.assertTrue(self.tcp_client)

        ###
        #   Add instrument specific code here.
        ###

        self.assert_direct_access_stop_telnet()


    def test_poll(self):
        '''
        No polling for a single sample
        '''


    def test_autosample(self):
        '''
        start and stop autosample and verify data particle
        '''


    def test_get_set_parameters(self):
        '''
        verify that all parameters can be get set properly, this includes
        ensuring that read only parameters fail on set.
        '''
        self.assert_enter_command_mode()


    def test_get_capabilities(self):
        """
        @brief Walk through all driver protocol states and verify capabilities
        returned by get_current_capabilities
        """
        self.assert_enter_command_mode()
