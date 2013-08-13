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
from mi.core.instrument.instrument_driver import DriverEvent
from mi.core.instrument.instrument_driver import DriverParameter

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

from pyon.core.exception import BadRequest
from pyon.core.exception import Conflict
from pyon.agent.agent import ResourceAgentState
from pyon.agent.agent import ResourceAgentEvent

# SAMPLE DATA FOR TESTING
from mi.instrument.pro_oceanus.pco2pro.pco2a.test.sample_data import *

GO_ACTIVE_TIMEOUT=3600
COMMAND_TIMEOUT=3600

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

# Used to validate param config retrieved from driver.
PARAMS = {
    Parameter.NUMBER_SAMPLES : int,
    Parameter.MENU_WAIT_TIME : int,
    Parameter.AUTO_SAMPLE_MODE : str,
    Parameter.ATMOSPHERE_MODE : int,
}
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

    def assert_particle_water_sample(self, data_particle, verify_values = False):
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
            ProtocolState.UNKNOWN: ['DRIVER_EVENT_DISCOVER',
                                    'PROTOCOL_EVENT_INIT_PARAMS'],
            ProtocolState.DISCOVERY: ['DRIVER_EVENT_START_DIRECT',
                                      'PROTOCOL_EVENT_WAIT_FOR_STATE',
                                      'PROTOCOL_EVENT_INIT_PARAMS'],
            ProtocolState.COMMAND: ['DRIVER_EVENT_CLOCK_SYNC',
                                    'DRIVER_EVENT_GET',
                                    'DRIVER_EVENT_SET',
                                    'DRIVER_EVENT_START_AUTOSAMPLE',
                                    'DRIVER_EVENT_START_DIRECT',
                                    'PROTOCOL_EVENT_INIT_PARAMS'],
            ProtocolState.AUTOSAMPLE: ['DRIVER_EVENT_STOP_AUTOSAMPLE',
                                       'PROTOCOL_EVENT_INIT_PARAMS'],
            ProtocolState.WAIT_FOR_COMMAND: ['PROTOCOL_EVENT_WAIT_FOR_STATE',
                                             'PROTOCOL_EVENT_INIT_PARAMS'],
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
                 
    #def test_startup_params(self):
        """
        Verify that startup parameters are applied correctly. 
        """
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
        """
    #def test_set(self):
        """
        Test all set commands. Verify all exception cases.
        """
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
        """
        
    #def test_clock_sync(self):
        """
        Make sure we can sync the clock
        """
        """
        self.assert_initialize_driver()
        
        # Do this multiple times to see if we can cause the failure
        # to sync the time to happen, then recover
        self.assert_driver_command(ProtocolEvent.CLOCK_SYNC)
        
        self.assert_driver_command(ProtocolEvent.CLOCK_SYNC)
        
        self.assert_driver_command(ProtocolEvent.CLOCK_SYNC)
        
        self.assert_driver_command(ProtocolEvent.CLOCK_SYNC)
        """
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
        log.info("Done initializing driver")
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
        self.assert_state_change(ProtocolState.COMMAND, timeout=COMMAND_TIMEOUT, sleep_time=60)
        
    #def test_discover_from_autosample_pre_sample(self):
        """
        Test that we can discover what mode we are in if the driver
         starts and the instrument is put into auto sampling but hasn't actually
         sampled yet.
        """
        """
        self.assert_initialize_driver()
        
        # make sure we are at the shortest auto sampling rate (excluding continuous)
        self.assert_set(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        # Put us in streaming
        # delay of 20 to make sure we actually enter auto sample and don't escape again
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE,
                                   state=ProtocolState.AUTOSAMPLE)
        
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
        """
    #def test_discover_from_autosample_post_sample(self):
        """
        Test that we can discover what mode we are in if the driver starts and the 
         instrument is auto sampling and has taken a sample.  In the post sample test, the
         driver must initially realize that it is in autosample mode and exit out of it
         into command mode.  In the pre_sample test, the driver gets the text to exit
         autosample mode while it is still discovering. 
        """
        """
        self.assert_initialize_driver()
        
        # make sure we are at the shortest auto sampling rate (excluding continuous)
        self.assert_set(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        # Put us in streaming
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE,
                                   state=ProtocolState.AUTOSAMPLE)
        
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
        """
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
        log.info("Discovered protocol state %s", state)
        # If we are in auto sample mode, try to break out of it
        # (this may take a while, up to your sampling time)
        if (state == ProtocolState.AUTOSAMPLE):
            log.debug("Stopping auto sample to go to command")
            reply = self.driver_client.cmd_dvr('execute_resource', ProtocolEvent.STOP_AUTOSAMPLE)
        
        # loop waiting until we discover command mode
        self.assert_state_change(ProtocolState.COMMAND, timeout=COMMAND_TIMEOUT, sleep_time=30)
        log.info("Found command state")
        # Just make really sure the driver is in command mode.
        self.assert_current_state(ProtocolState.COMMAND)
        
    def assert_state_change(self, target_state, timeout, sleep_time=2):
        """
        Verify the driver state changes within a given timeout period.
        Fail if the state doesn't change to the expected state.
        @param target_state: State we expect the protocol to be in
        @param timeout: how long to wait for the driver to change states
        @param sleep_time: amount of time to sleep in between checking
        the state, defaults to 2 seconds
        """
        sent_stop_autosample = False
        end_time = time.time() + timeout

        while(time.time() <= end_time):
            state = self.driver_client.cmd_dvr('get_resource_state')
            if(state == target_state):
                log.info("Current state match: %s", state)
                return
            if(target_state == ProtocolState.COMMAND and
               state == ProtocolState.AUTOSAMPLE and
               not sent_stop_autosample):
                log.info("Stopping auto sample to go to command")
                reply = self.driver_client.cmd_dvr('execute_resource', ProtocolEvent.STOP_AUTOSAMPLE)
                # only need to send once, set a flag to make sure we don't keep sending
                sent_stop_autosample = True
            log.info("state mismatch %s != %s, sleep for a bit", state, target_state)
            time.sleep(sleep_time)

        log.error("Failed to transition state to %s, current state: %s", target_state, state)
        self.fail("Failed to transition state to %s, current state: %s" % (target_state, state))


###############################################################################
#                            QUALIFICATION TESTS                              #
# Device specific qualification tests are for doing final testing of ion      #
# integration.  The generally aren't used for instrument debugging and should #
# be tackled after all unit and integration tests are complete                #
###############################################################################
@attr('QUAL', group='mi')
class Pco2aQualificationTest(InstrumentDriverQualificationTestCase, Pco2aTestMixin):
    def setUp(self):
        InstrumentDriverQualificationTestCase.setUp(self)
        
    def assert_enter_command_mode(self, timeout=GO_ACTIVE_TIMEOUT):
        '''
        Walk through IA states to get to command mode from uninitialized
        Override the default unit test in order to handle discovery busy state
        and wait until streaming or idle is found
        '''
        state = self.instrument_agent_client.get_agent_state()
        if state == ResourceAgentState.UNINITIALIZED:

            with self.assertRaises(Conflict):
                res_state = self.instrument_agent_client.get_resource_state()

            cmd = AgentCommand(command=ResourceAgentEvent.INITIALIZE)
            retval = self.instrument_agent_client.execute_agent(cmd, timeout=timeout)
            state = self.instrument_agent_client.get_agent_state()
            self.assertEqual(state, ResourceAgentState.INACTIVE)
            log.info("Sent INITIALIZE; IA state = %s", state)
    
            res_state = self.instrument_agent_client.get_resource_state()
            self.assertEqual(res_state, DriverConnectionState.UNCONFIGURED)
    
            cmd = AgentCommand(command=ResourceAgentEvent.GO_ACTIVE)
            retval = self.instrument_agent_client.execute_agent(cmd, timeout=timeout)
            state = self.instrument_agent_client.get_agent_state()
            log.info("Sent GO_ACTIVE; IA state = %s", state)
        
        if state == ResourceAgentState.BUSY:
            # We are still discovering, need to wait until either streaming
            # or idle is found
            log.debug("Waiting for streaming or idle...")
            end_time = time.time() + COMMAND_TIMEOUT
            while(state not in [ResourceAgentState.STREAMING,
                                ResourceAgentState.IDLE,
                                ResourceAgentState.COMMAND]):
                log.debug("Not found yet, state is %s, sleeping more...", state)
                time.sleep(5)
                state = self.instrument_agent_client.get_agent_state()
                if time.time() > end_time:
                    self.fail("Timeout waiting to discover state")
                    
        log.info("Found agent state %s", state)            
        if state == ResourceAgentState.STREAMING:
            # The instrument is in autosample; take it out of autosample,
            # which will cause the driver and agent to transition to COMMAND
            log.debug("Stopping auto sample")
            self.assert_stop_autosample(timeout=COMMAND_TIMEOUT)
        elif state == ResourceAgentState.IDLE:
            log.debug("Sending run")
            cmd = AgentCommand(command=ResourceAgentEvent.RUN)
            retval = self.instrument_agent_client.execute_agent(cmd)

        state = self.instrument_agent_client.get_agent_state()
        log.info("About to check for command; IA state = %s", state)
        self.assertEqual(state, ResourceAgentState.COMMAND)

        res_state = self.instrument_agent_client.get_resource_state()
        self.assertEqual(res_state, DriverProtocolState.COMMAND)
        
    def assert_discover(self, expected_agent_state, expected_resource_state=None):
        """
        Walk an agent through go active and verify the resource state.
        @return:
        """
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.UNINITIALIZED)

        self.assert_switch_state(ResourceAgentEvent.INITIALIZE, ResourceAgentState.INACTIVE,
                                 DriverConnectionState.UNCONFIGURED)

        # Looks like some drivers go directly to streaming after a run.  Is this correct behavior.
        self.assert_switch_state(ResourceAgentEvent.GO_ACTIVE, [ResourceAgentState.IDLE,
                                                                ResourceAgentState.STREAMING,
                                                                ResourceAgentState.BUSY])

        state = self.instrument_agent_client.get_agent_state();
        log.debug("In assert discover, state after GO_ACTIVE is %s", state)
        if(self.instrument_agent_client.get_agent_state() == ResourceAgentState.BUSY):
            # We are still discovering, need to wait until either streaming
            # or idle is found
            end_time = time.time() + (COMMAND_TIMEOUT*2)
            state = self.instrument_agent_client.get_agent_state()
            while(state not in [ResourceAgentState.STREAMING, ResourceAgentState.IDLE]):
                log.debug("Streaming or idle not found yet, sleeping more...")
                time.sleep(5)
                state = self.instrument_agent_client.get_agent_state()
                if time.time() > end_time:
                    self.fail("Timeout waiting to discover state")
                    
        log.debug("In assert discover, state is %s", state)            
        if(self.instrument_agent_client.get_agent_state() == ResourceAgentState.IDLE):
            log.debug("switching from IDLE to RUN")
            self.assert_switch_state(ResourceAgentEvent.RUN, expected_agent_state,
                                     expected_resource_state)
           
    def test_discover(self):
        """
        verify we can discover our instrument state from autosample.
        Override the unit_test because although this instrument has a streaming
        mode, we only rarely can discover streaming (it is timing dependent)
        """
        
        # Verify the agent is in command mode
        self.assert_enter_command_mode()

        # Now reset and try to discover.  This will stop the driver which 
        # holds the current instrument state.
        self.assert_reset()
        self.assert_discover(ResourceAgentState.COMMAND)
        
        #make this test not take quite so long by setting to 1/2 hr rather than 1 hr sampling
        self.assert_set_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        
        # go to autosampling, but don't wait for a sample before switching
        # back to command
        self.assert_start_autosample()
        log.debug("Done switching to autosample")
        self.assert_reset()
        # Now discover command mode
        self.assert_discover(ResourceAgentState.COMMAND)
        log.debug("Done discovering command")
        
        # I think something is happening where the command thread discovers command state,
        # but is still setting parameters for initialization after it discovers the state.  
        # If we don't sleep here, the following set parameter fails.  This should probably be 
        # fixed in the driver, but for now since this is on hold leave the sleep in here to
        # fix this test.
        time.sleep(120)

        #make this test not take quite so long by setting to 1/2 hr rather than 1 hr sampling
        self.assert_set_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        
        # go to autosampling, and wait for a sample before switching back
        # to command
        self.assert_start_autosample()
        # just need to wait for one type of particle, they both arrive at the same time
        self.assert_particle_async(DataParticleType.PCO2A_WATER_SAMPLES,
                                   self.assert_particle_water_sample, timeout=3600)
        
        self.assert_reset()
        # Now discover command mode
        self.assert_discover(ResourceAgentState.COMMAND)
     
    def test_direct_access_telnet_mode_command(self):
        """
        This test manually tests that the Instrument Driver properly supports
        direct access to the physical instrument starting from command state.
        """
        
        self.assert_enter_command_mode()
        self.assert_set_parameter(Parameter.NUMBER_SAMPLES, 3)
        
        self.assert_direct_access_start_telnet()
        self.assertTrue(self.tcp_client)
        log.info("Successfully entered DA start telnet")

        # navigate through menus to set the number of samples
        self.tcp_client.send_data(" ")
        result = self.tcp_client.expect(Prompt.MAIN_MENU, sleep_time=10)
	self.assertTrue(result)
        self.tcp_client.send_data("5")
        result = self.tcp_client.expect(Prompt.AUTO_START_MENU, sleep_time=10)
        self.assertTrue(result)
        self.tcp_client.send_data("2")
        result = self.tcp_client.expect(Prompt.CHANGE_NUMBER_SAMPLES, sleep_time=10)
        self.assertTrue(result)
        # set number of samples to 7
        self.tcp_client.send_data("7")
        result = self.tcp_client.expect(Prompt.AUTO_START_MENU, sleep_time=10)
        self.assertTrue(result)
        log.info("Set number of samples to 7")
        
        self.assert_direct_access_stop_telnet()
        
        self.assert_state_change(ResourceAgentState.COMMAND, ProtocolState.COMMAND, 10)
        # no direct access parameters, so number of samples will not change back to 3
        
        ###
        # Test direct access inactivity timeout
        ###
        self.assert_direct_access_start_telnet(inactivity_timeout=30, session_timeout=90)
        self.assert_state_change(ResourceAgentState.COMMAND, ProtocolState.COMMAND, 60)

        ###
        # Test session timeout without activity
        ###
        self.assert_direct_access_start_telnet(inactivity_timeout=120, session_timeout=30)
        self.assert_state_change(ResourceAgentState.COMMAND, ProtocolState.COMMAND, 60)
        
        ###
        # Test direct access session timeout with activity
        ###
        self.assert_direct_access_start_telnet(inactivity_timeout=30, session_timeout=60)
        # Send some activity every 15 seconds to keep DA alive.
        for i in range(1, 2, 3):
            self.tcp_client.send_data(" ")
            log.debug("Sending a little keep alive communication, sleeping for 15 seconds")
            time.sleep(15)

        self.assert_state_change(ResourceAgentState.COMMAND, ProtocolState.COMMAND, 45)
        
        ###
        # Test direct access disconnect
        ###
        self.assert_direct_access_start_telnet()
        self.tcp_client.disconnect()
        self.assert_state_change(ResourceAgentState.COMMAND, ProtocolState.COMMAND, 30)

    def test_direct_access_telnet_mode_autosample(self):
        """
        This test manually tests that the Instrument Driver properly supports
        direct access to the physical instrument starting from command state,
        entering auto sample mode during the direct access session, then coming
        out of direct access and getting back to command state.
        """
        
        self.assert_enter_command_mode()
        # make this test a little shorter by selecting a half hour sample
        self.assert_set_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        
        self.assert_direct_access_start_telnet()
        self.assertTrue(self.tcp_client)
        log.info("Successfully entered DA start telnet")

        # navigate through menus to set the number of samples
        self.tcp_client.send_data(" ")
        result = self.tcp_client.expect(Prompt.MAIN_MENU, sleep_time=10)
	self.assertTrue(result)
        self.tcp_client.send_data("7")
        log.debug("Sent go to sleep")
        # wait for escape from auto sample time to elapse so we stay in autosample
        time.sleep(20)
        
        # stop direct access
        self.assert_direct_access_stop_telnet()
        
        # make sure we re-discover command state
        self.assert_state_change(ResourceAgentState.COMMAND, ProtocolState.COMMAND,
                                 COMMAND_TIMEOUT)

    def test_autosample(self):
        '''
        start and stop autosample and verify data particle
        '''
        
        self.data_subscribers.start_data_subscribers()
        self.addCleanup(self.data_subscribers.stop_data_subscribers)
        
        self.assert_enter_command_mode()
        log.debug("Entered command state")
        
        self.data_subscribers.clear_sample_queue(DataParticleType.PCO2A_WATER_SAMPLES)
        self.data_subscribers.clear_sample_queue(DataParticleType.PCO2A_AIR_SAMPLES)
        
        #make this test not take quite so long by setting to 1/2 hr rather than 1 hr sampling
        self.assert_set_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        log.debug("Set to 1/2 hour sample")

        # Begin streaming.
        cmd = AgentCommand(command=ProtocolEvent.START_AUTOSAMPLE)
        retval = self.instrument_agent_client.execute_resource(cmd, timeout=30)

        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.STREAMING)
        
        # Assert we got at least 3 samples.
        samples = self.data_subscribers.get_samples(DataParticleType.PCO2A_WATER_SAMPLES, 3,
                                                    timeout=3600)
        self.assertGreaterEqual(len(samples), 3)
        
        self.assert_particle_water_sample(samples.pop())
        self.assert_particle_water_sample(samples.pop())
        self.assert_particle_water_sample(samples.pop())
        
        self.data_subscribers.clear_sample_queue(DataParticleType.PCO2A_WATER_SAMPLES)
        
        samples = self.data_subscribers.get_samples(DataParticleType.PCO2A_AIR_SAMPLES, 3,
                                                    timeout=3600)
        self.assertGreaterEqual(len(samples), 3)
        
        self.assert_particle_air_sample(samples.pop())
        self.assert_particle_air_sample(samples.pop())
        self.assert_particle_air_sample(samples.pop())
        
        # Halt streaming.
        cmd = AgentCommand(command=ProtocolEvent.STOP_AUTOSAMPLE)
        retval = self.instrument_agent_client.execute_resource(cmd, timeout=30)

        state = self.instrument_agent_client.get_agent_state()
        starttime = time.time()
        while (state == ResourceAgentState.STREAMING):
            log.debug("Waiting for STREAMING, current state %s", state)
            time.sleep(5)
            state = self.instrument_agent_client.get_agent_state()
            
            if (time.time() > starttime + COMMAND_TIMEOUT):
                log.error("Timeout waiting for command state")
                self.fail("Timeout waiting for command state")
                   
        self.assertEqual(state, ResourceAgentState.COMMAND)
        
        self.assert_reset()

        
    def assertParamDict(self, pd, all_params=False):
        """
        Verify all device parameters exist and are correct type.
        """
        if all_params:
            self.assertEqual(set(pd.keys()), set(PARAMS.keys()))
            for (key, type_val) in PARAMS.iteritems():
                if type_val == list or type_val == tuple:
                    self.assertTrue(isinstance(pd[key], (list, tuple)))
                else:
                    self.assertTrue(isinstance(pd[key], type_val))

        else:
            for (key, val) in pd.iteritems():
                self.assertTrue(PARAMS.has_key(key))
                self.assertTrue(isinstance(val, PARAMS[key]))
                
    def assertParamVals(self, params, correct_params):
        """
        Verify parameters take the correct values.
        """
        self.assertEqual(set(params.keys()), set(correct_params.keys()))
        for (key, val) in params.iteritems():
            correct_val = correct_params[key]
            if isinstance(val, float):
                # Verify to 5% of the larger value.
                max_val = max(abs(val), abs(correct_val))
                self.assertAlmostEqual(val, correct_val, delta=max_val*.01)

            elif isinstance(val, (list, tuple)):
                # list of tuple.
                self.assertEqual(list(val), list(correct_val))

            else:
                # int, bool, str.
                self.assertEqual(val, correct_val)
                      
    def test_get_set_parameters(self):
        '''
        verify that all parameters can be get set properly.
        '''
        
        self.assert_enter_command_mode()
        
        # Retrieve all resource parameters.
        reply = self.instrument_agent_client.get_resource(Parameter.ALL)
        self.assertParamDict(reply, True)
        orig_config = reply

        # Retrieve a subset of resource parameters.
        params = [
            Parameter.NUMBER_SAMPLES,
            Parameter.MENU_WAIT_TIME,
        ]
        reply = self.instrument_agent_client.get_resource(params)
        self.assertParamDict(reply)
        orig_params = reply

        # Set a subset of resource parameters.
        new_params = {
            Parameter.NUMBER_SAMPLES : orig_params[Parameter.NUMBER_SAMPLES] + 1,
            Parameter.MENU_WAIT_TIME : orig_params[Parameter.MENU_WAIT_TIME] + 10
        }
        self.instrument_agent_client.set_resource(new_params)
        check_new_params = self.instrument_agent_client.get_resource(params)
        self.assertParamVals(check_new_params, new_params)

        # Reset the parameters back to their original values.
        self.instrument_agent_client.set_resource(orig_params)
        reply = self.instrument_agent_client.get_resource(Parameter.ALL)
        reply.pop(Parameter.NUMBER_SAMPLES)
        orig_config.pop(Parameter.NUMBER_SAMPLES)
        self.assertParamVals(reply, orig_config)
        
        self.assert_reset()


    def test_get_capabilities(self):
        """
        @brief Walk through all driver protocol states and verify capabilities
        returned by get_current_capabilities
        """
        
        agt_pars_all = ['aggstatus', 'alerts', 'example', 'pubrate', 'streams']

        # UNINITIALIZED
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.UNINITIALIZED)

        capabilities = {
            'agent_command' : [ResourceAgentEvent.INITIALIZE],
            'agent_parameter' : agt_pars_all,
        }
       
        # verify uninitialized capabilities
        self.assert_capabilities(capabilities)
        
        cmd = AgentCommand(command=ResourceAgentEvent.INITIALIZE)
        retval = self.instrument_agent_client.execute_agent(cmd)

        # INACTIVE
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.INACTIVE)
               
        capabilities = {
            'agent_command' : [ResourceAgentEvent.GO_ACTIVE,
                              ResourceAgentEvent.RESET],
            'agent_parameter' : agt_pars_all
        }
        
        # verify uninitialized capabilities
        self.assert_capabilities(capabilities)

        cmd = AgentCommand(command=ResourceAgentEvent.GO_ACTIVE)
        retval = self.instrument_agent_client.execute_agent(cmd)
        
        state = self.instrument_agent_client.get_agent_state()
        if state == ResourceAgentState.BUSY:
            # BUSY
            capabilities = {
                'agent_command' : [],
                'agent_parameter' : agt_pars_all,
                'resource_command' : [ProtocolEvent.START_DIRECT],
                'resource_parameter' : PARAMS.keys()
            }
            # verify uninitialized capabilities
            self.assert_capabilities(capabilities)
        
            # We are still discovering, need to wait until either streaming
            # or idle is found
            log.debug("Waiting for streaming or idle...")
            end_time = time.time() + COMMAND_TIMEOUT
            while(state not in [ResourceAgentState.IDLE,
                                ResourceAgentState.COMMAND]):
                log.debug("Waiting for streaming or idle, state is %s, sleeping more...", state)
                time.sleep(5)
                state = self.instrument_agent_client.get_agent_state()
                if time.time() > end_time:
                    self.fail("Timeout waiting to discover state")
                              
        if state == ResourceAgentState.IDLE:
            # IDLE

            capabilities = {
                'agent_command' : [ResourceAgentEvent.GO_INACTIVE,
                              ResourceAgentEvent.RESET,
                              ResourceAgentEvent.RUN],
                'agent_parameter' : agt_pars_all
            }
        
            # verify uninitialized capabilities
            self.assert_capabilities(capabilities)
        
            cmd = AgentCommand(command=ResourceAgentEvent.RUN)
            retval = self.instrument_agent_client.execute_agent(cmd)
            
        # COMMAND   
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.COMMAND)

        capabilities = {
            'agent_command' : [ResourceAgentEvent.CLEAR,
                               ResourceAgentEvent.RESET,
                               ResourceAgentEvent.GO_DIRECT_ACCESS,
                               ResourceAgentEvent.GO_INACTIVE,
                               ResourceAgentEvent.PAUSE],
            'agent_parameter' : agt_pars_all,
            'resource_command' : [ProtocolEvent.START_AUTOSAMPLE,
                                  ProtocolEvent.GET,
                                  ProtocolEvent.SET,
                                  ProtocolEvent.CLOCK_SYNC,
                                  ProtocolEvent.START_DIRECT],
            'resource_parameter' : PARAMS.keys()
        }
       
        # verify uninitialized capabilities
        self.assert_capabilities(capabilities)
        
        #make this test not take quite so long by setting to 1/2 hr rather than 1 hr sampling
        self.assert_set_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        
        cmd = AgentCommand(command=ProtocolEvent.START_AUTOSAMPLE)
        retval = self.instrument_agent_client.execute_resource(cmd, timeout=40)
        
        # STREAMING
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.STREAMING)
        
        capabilities = {
            'agent_command' : [ResourceAgentEvent.RESET,
                               ResourceAgentEvent.GO_INACTIVE],
            'agent_parameter' : agt_pars_all,
            'resource_command' : [ProtocolEvent.STOP_AUTOSAMPLE],
            'resource_parameter' : PARAMS.keys()
        }
        
        # verify uninitialized capabilities
        self.assert_capabilities(capabilities)

        time.sleep(5)

        cmd = AgentCommand(command=ProtocolEvent.STOP_AUTOSAMPLE)
        retval = self.instrument_agent_client.execute_resource(cmd, timeout=40)
        
        # COMMAND, AFTER STREAMING
        state = self.instrument_agent_client.get_agent_state()
        end_time = time.time() + COMMAND_TIMEOUT
        while(state not in [ResourceAgentState.COMMAND]):
            log.debug("Waiting to exit streaming, state is %s, sleeping more...", state)
            time.sleep(5)
            state = self.instrument_agent_client.get_agent_state()
            if state == ResourceAgentState.COMMAND:
                log.debug("Found command state")
                break          
            if time.time() > end_time:
                self.fail("Timeout waiting to discover command state")
                
        self.assertEqual(state, ResourceAgentState.COMMAND)

        capabilities = {
            'agent_command' : [ResourceAgentEvent.CLEAR,
                              ResourceAgentEvent.RESET,
                              ResourceAgentEvent.GO_DIRECT_ACCESS,
                              ResourceAgentEvent.GO_INACTIVE,
                              ResourceAgentEvent.PAUSE],
            'agent_parameter' : agt_pars_all,
            'resource_command' : [ProtocolEvent.START_AUTOSAMPLE,
                                  ProtocolEvent.GET,
                                  ProtocolEvent.SET,
                                  ProtocolEvent.CLOCK_SYNC,
                                  ProtocolEvent.START_DIRECT],
            'resource_parameter' : PARAMS.keys()
        }
       
        # verify uninitialized capabilities
        self.assert_capabilities(capabilities)
        
        cmd = AgentCommand(command=ResourceAgentEvent.RESET)
        retval = self.instrument_agent_client.execute_agent(cmd)
        
        # UNINITIALIZED
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.UNINITIALIZED)
        
        capabilities = {
            'agent_command' : [ResourceAgentEvent.INITIALIZE],
            'agent_parameter' : agt_pars_all,
        }
       
        # verify uninitialized capabilities
        self.assert_capabilities(capabilities)
        
    def test_enum(self):
        """
        check that enums for protocol states and events and parameters match
        corresponding Driver states and events, and that enums are unique.
        """

        # check protocol states
        self.assertEqual(ProtocolState.UNKNOWN, DriverProtocolState.UNKNOWN)
        self.assertEqual(ProtocolState.COMMAND, DriverProtocolState.COMMAND)
        self.assertEqual(ProtocolState.AUTOSAMPLE, DriverProtocolState.AUTOSAMPLE)
        self.assertEqual(ProtocolState.DIRECT_ACCESS, DriverProtocolState.DIRECT_ACCESS)
        
        self.assertTrue(self.check_for_reused_values(DriverProtocolState))
        self.assertTrue(self.check_for_reused_values(ProtocolState))
        
        # check protocol events
        self.assertEqual(ProtocolEvent.ENTER, DriverEvent.ENTER)
        self.assertEqual(ProtocolEvent.EXIT, DriverEvent.EXIT)
        self.assertEqual(ProtocolEvent.GET, DriverEvent.GET)
        self.assertEqual(ProtocolEvent.SET, DriverEvent.SET)
        self.assertEqual(ProtocolEvent.DISCOVER, DriverEvent.DISCOVER)
        self.assertEqual(ProtocolEvent.START_AUTOSAMPLE, DriverEvent.START_AUTOSAMPLE)
        self.assertEqual(ProtocolEvent.STOP_AUTOSAMPLE, DriverEvent.STOP_AUTOSAMPLE)
        self.assertEqual(ProtocolEvent.EXECUTE_DIRECT, DriverEvent.EXECUTE_DIRECT)
        self.assertEqual(ProtocolEvent.START_DIRECT, DriverEvent.START_DIRECT)
        self.assertEqual(ProtocolEvent.STOP_DIRECT, DriverEvent.STOP_DIRECT)
        self.assertEqual(ProtocolEvent.CLOCK_SYNC, DriverEvent.CLOCK_SYNC)

        self.assertTrue(self.check_for_reused_values(DriverEvent))
        self.assertTrue(self.check_for_reused_values(ProtocolEvent))
        
        # check parameters
        self.assertEqual(Parameter.ALL, DriverParameter.ALL)

        self.assertTrue(self.check_for_reused_values(DriverParameter))
        self.assertTrue(self.check_for_reused_values(Parameter))
    
    def check_for_reused_values(self, obj):
        """
        @author Roger Unwin
        @brief  verifies that no two definitions resolve to the same value.
        @returns True if no reused values
        """
        match = 0
        outer_match = 0
        for i in [v for v in dir(obj) if not callable(getattr(obj,v))]:
            if i.startswith('_') == False:
                outer_match = outer_match + 1
                for j in [x for x in dir(obj) if not callable(getattr(obj,x))]:
                    if i.startswith('_') == False:
                        if getattr(obj, i) == getattr(obj, j):
                            match = match + 1
                            log.debug(str(i) + " == " + j + " (Looking for reused values)")

        # If this assert fails, then two of the enumerations have an identical value...
        return match == outer_match
 
    def test_instrument_driver_vs_invalid_commands(self):
        """
        @Author Edward Hunter
        @brief This test should send mal-formed, misspelled,
               missing parameter, or out of bounds parameters
               at the instrument driver in an attempt to
               confuse it.

               See: test_instrument_driver_to_physical_instrument_interoperability
               That test will provide the how-to of connecting.
               Once connected, send messed up commands.

               * negative testing

               Test illegal behavior and replies.
        """

        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.UNINITIALIZED)

        # Try to execute agent command with bogus command.
        with self.assertRaises(BadRequest):
            cmd = AgentCommand(command='BOGUS_COMMAND')
            log.debug("Defined bogus command")
            retval = self.instrument_agent_client.execute_agent(cmd)
            log.debug("sent bogus command")

        log.debug("done with bogus command")
        # Can't go active in unitialized state.
        # Status 660 is state error.
        with self.assertRaises(Conflict):
            cmd = AgentCommand(command=ResourceAgentEvent.GO_ACTIVE)
            retval = self.instrument_agent_client.execute_agent(cmd)

        # Try to execute the resource, wrong state.
        with self.assertRaises(BadRequest):
            cmd = AgentCommand(command=ProtocolEvent.START_AUTOSAMPLE)
            retval = self.instrument_agent_client.execute_agent(cmd)

        cmd = AgentCommand(command=ResourceAgentEvent.INITIALIZE)
        retval = self.instrument_agent_client.execute_agent(cmd)
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.INACTIVE)

        cmd = AgentCommand(command=ResourceAgentEvent.GO_ACTIVE)
        retval = self.instrument_agent_client.execute_agent(cmd)
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.IDLE)

        cmd = AgentCommand(command=ResourceAgentEvent.RUN)
        retval = self.instrument_agent_client.execute_agent(cmd)
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.COMMAND)

        # 404 unknown agent command.
        with self.assertRaises(BadRequest):
            cmd = AgentCommand(command='bad_command')
            reply = self.instrument_agent_client.execute_agent(cmd)
                    
    def test_startup_params(self):
        """
        Verify that startup parameters are applied correctly when the driver is started.
        """
        # Startup the driver, verify the startup value and then change it.
        self.assert_enter_command_mode()
        # get startup parameters
        self.assert_get_parameter(Parameter.NUMBER_SAMPLES, 5)
        self.assert_get_parameter(Parameter.MENU_WAIT_TIME, 20)
        self.assert_get_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.ONE_HR_SAMPLE)
        self.assert_get_parameter(Parameter.ATMOSPHERE_MODE, 2)
        # change startup values
        self.assert_set_parameter(Parameter.NUMBER_SAMPLES, 3)
        self.assert_set_parameter(Parameter.MENU_WAIT_TIME, 40)
        self.assert_set_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.HALF_HR_SAMPLE)
        self.assert_set_parameter(Parameter.ATMOSPHERE_MODE, 1)

        # Reset the agent which brings the driver down
        self.assert_reset()

        # Now restart the driver and verify the value has reverted back to the startup value
        self.assert_enter_command_mode()
        # get startup parameters
        self.assert_get_parameter(Parameter.NUMBER_SAMPLES, 5)
        self.assert_get_parameter(Parameter.MENU_WAIT_TIME, 20)
        self.assert_get_parameter(Parameter.AUTO_SAMPLE_MODE, AutoSampleMode.ONE_HR_SAMPLE)
        self.assert_get_parameter(Parameter.ATMOSPHERE_MODE, 2)


