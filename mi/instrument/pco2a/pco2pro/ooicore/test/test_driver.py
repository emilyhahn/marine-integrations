"""
@package mi.instrument.pco2a.pco2pro.ooicore.test.test_driver
@file marine-integrations/mi/instrument/pco2a/pco2pro/ooicore/driver.py
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

import unittest

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

from mi.core.exceptions import InstrumentProtocolException
from mi.core.exceptions import InstrumentParameterException

from ion.agents.instrument.instrument_agent import InstrumentAgentState
from ion.agents.instrument.direct_access.direct_access_server import DirectAccessTypes

from mi.instrument.pco2a.pco2pro.ooicore.driver import Pco2aInstrumentDriver
from mi.instrument.pco2a.pco2pro.ooicore.driver import DataParticleType
from mi.instrument.pco2a.pco2pro.ooicore.driver import ProtocolState
from mi.instrument.pco2a.pco2pro.ooicore.driver import ProtocolEvent
from mi.instrument.pco2a.pco2pro.ooicore.driver import Capability
from mi.instrument.pco2a.pco2pro.ooicore.driver import Parameter
from mi.instrument.pco2a.pco2pro.ooicore.driver import Protocol
from mi.instrument.pco2a.pco2pro.ooicore.driver import Prompt
from mi.instrument.pco2a.pco2pro.ooicore.driver import NEWLINE
from mi.instrument.pco2a.pco2pro.ooicore.driver import AutoSampleMode
from mi.instrument.pco2a.pco2pro.ooicore.driver import AUTO_SAMPLE_MENU_OPTS
from mi.instrument.pco2a.pco2pro.ooicore.driver import AUTO_SAMPLE_STR
from mi.instrument.pco2a.pco2pro.ooicore.driver import Command
from mi.instrument.pco2a.pco2pro.ooicore.driver import SubMenu
from mi.instrument.pco2a.pco2pro.ooicore.driver import MENU
from mi.instrument.pco2a.pco2pro.ooicore.driver import COMMAND_CHAR
from mi.instrument.pco2a.pco2pro.ooicore.driver import Pco2aAirSampleDataParticleKey
from mi.instrument.pco2a.pco2pro.ooicore.driver import Pco2aWaterSampleDataParticleKey

# SAMPLE DATA FOR TESTING
from mi.instrument.pco2a.pco2pro.ooicore.test.sample_data import *

###
#   Driver parameters for the tests
###
InstrumentDriverTestCase.initialize(
    driver_module='mi.instrument.pco2a.pco2pro.ooicore.driver',
    driver_class="InstrumentDriver",

    instrument_agent_resource_id = 'HYBCAE',
    instrument_agent_name = 'pco2a_pco2pro_ooicore',
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
class DriverTestMixinSub(DriverTestMixin):
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
                                      DEFAULT: AutoSampleMode.HALF_HR_SAMPLE,
                                      VALUE: AutoSampleMode.HALF_HR_SAMPLE}
        }
    _driver_capabilities = {
        # capabilities defined in the IOS
        Capability.START_AUTOSAMPLE : {STATES: [ProtocolState.COMMAND,
                                                ProtocolState.AUTOSAMPLE]},
        Capability.STOP_AUTOSAMPLE : {STATES: [ProtocolState.COMMAND,
                                               ProtocolState.AUTOSAMPLE]},
        Capability.CLOCK_SYNC : {STATES: [ProtocolState.COMMAND]},
        Capability.ACQUIRE_STATUS : {STATES: [ProtocolState.COMMAND]},
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
    def assert_driver_parameters(self, current_parameters,
                                 verify_values = False):
        """
        Verify that all driver parameters are correct and potentially verify
        values.
        @param current_parameters: driver parameters read from the driver
        instance
        @param verify_values: should we verify values against definition?
        """
        self.assert_parameters(current_parameters, self._driver_parameters,
                               verify_values)

    def assert_particle_air_sample(self, data_particle, verify_values = False):
        '''
        Verify air_sample particle
        @param data_particle:  Pco2aAirSampleDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(Pco2aAirSampleDataParticleKey,
                                       self._air_sample_params)
        self.assert_data_particle_header(data_particle,
                                         DataParticleType.AIR_SAMPLE)
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
                                         DataParticleType.WATER_SAMPLE)
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
class DriverUnitTest(InstrumentDriverUnitTestCase, DriverTestMixinSub):
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
            ProtocolState.UNKNOWN: ['PROTOCOL_EVENT_DISCOVER_COMMAND'],
            ProtocolState.DISCOVERY: ['DRIVER_EVENT_START_DIRECT',
                                      'PROTOCOL_EVENT_DISCOVER_COMMAND'],
            ProtocolState.COMMAND: ['DRIVER_EVENT_ACQUIRE_STATUS',
                                    'DRIVER_EVENT_CLOCK_SYNC',
                                    'DRIVER_EVENT_GET',
                                    'DRIVER_EVENT_SET',
                                    'DRIVER_EVENT_START_AUTOSAMPLE',
                                    'DRIVER_EVENT_START_DIRECT'],
            ProtocolState.AUTOSAMPLE: ['DRIVER_EVENT_STOP_AUTOSAMPLE'],
            ProtocolState.WAIT_FOR_COMMAND: [],
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
	self.assertRaises(InstrumentProtocolException,
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
        



###############################################################################
#                            INTEGRATION TESTS                                #
#     Integration test test the direct driver / instrument interaction        #
#     but making direct calls via zeromq.                                     #
#     - Common Integration tests test the driver through the instrument agent #
#     and common for all drivers (minimum requirement for ION ingestion)      #
###############################################################################
@attr('INT', group='mi')
class DriverIntegrationTest(InstrumentDriverIntegrationTestCase):
    def setUp(self):
        InstrumentDriverIntegrationTestCase.setUp(self)



###############################################################################
#                            QUALIFICATION TESTS                              #
# Device specific qualification tests are for doing final testing of ion      #
# integration.  The generally aren't used for instrument debugging and should #
# be tackled after all unit and integration tests are complete                #
###############################################################################
@attr('QUAL', group='mi')
class DriverQualificationTest(InstrumentDriverQualificationTestCase):
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
