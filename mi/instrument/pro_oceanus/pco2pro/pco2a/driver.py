"""
@package mi.instrument.pro_oceanus.pco2pro.pco2a.driver
@file marine-integrations/mi/instrument/pro_oceanus/pco2pro/pco2a/driver.py
@author E. Hahn
@brief Driver for the ooicore
Release notes:

Initial revision.
"""

__author__ = 'E. Hahn'
__license__ = 'Apache 2.0'

import re
import string
import time
import thread

from mi.core.log import get_logger ; log = get_logger()
from mi.core.time import get_timestamp_delayed

from mi.core.exceptions import SampleException
from mi.core.exceptions import InstrumentProtocolException
from mi.core.exceptions import InstrumentParameterException
from mi.core.exceptions import InstrumentTimeoutException

from mi.core.common import BaseEnum
from mi.core.instrument.instrument_protocol import CommandResponseInstrumentProtocol
from mi.core.instrument.instrument_fsm import ThreadSafeFSM
from mi.core.instrument.instrument_driver import SingleConnectionInstrumentDriver
from mi.core.instrument.instrument_driver import DriverEvent
from mi.core.instrument.instrument_driver import DriverAsyncEvent
from mi.core.instrument.instrument_driver import DriverProtocolState
from mi.core.instrument.instrument_driver import DriverParameter
from mi.core.instrument.instrument_driver import ResourceAgentState
from mi.core.instrument.data_particle import DataParticle
from mi.core.instrument.data_particle import DataParticleKey
from mi.core.instrument.data_particle import CommonDataParticleType
from mi.core.instrument.chunker import StringChunker
from mi.core.instrument.protocol_param_dict import ParameterDictVisibility
from mi.core.instrument.protocol_param_dict import ParameterDictType
from mi.core.instrument.protocol_param_dict import ProtocolParameterDict
from mi.core.instrument.instrument_protocol import MenuInstrumentProtocol
from mi.core.instrument.driver_dict import DriverDictKey


# newline.
NEWLINE = '\r\n'

# default timeout.
TIMEOUT = 10

Directions = MenuInstrumentProtocol.MenuTree.Directions

###
#    Driver Constant Definitions
###

class DataParticleType(BaseEnum):
    """
    Data particle types produced by this driver
    """
    RAW = CommonDataParticleType.RAW
    PCO2A_AIR_SAMPLES = 'pco2a_air_samples'
    PCO2A_WATER_SAMPLES = 'pco2a_water_samples'

class ProtocolState(BaseEnum):
    """
    Instrument protocol states
    """
    UNKNOWN = DriverProtocolState.UNKNOWN
    DISCOVERY = "DRIVER_STATE_DISCOVERY"
    COMMAND = DriverProtocolState.COMMAND
    WAIT_FOR_COMMAND = "DRIVER_STATE_WAIT_FOR_COMMAND"
    AUTOSAMPLE = DriverProtocolState.AUTOSAMPLE
    DIRECT_ACCESS = DriverProtocolState.DIRECT_ACCESS

class ProtocolEvent(BaseEnum):
    """
    Protocol events
    """
    ENTER = DriverEvent.ENTER
    EXIT = DriverEvent.EXIT
    GET = DriverEvent.GET
    SET = DriverEvent.SET
    DISCOVER = DriverEvent.DISCOVER
    START_DIRECT = DriverEvent.START_DIRECT
    STOP_DIRECT = DriverEvent.STOP_DIRECT
    EXECUTE_DIRECT = DriverEvent.EXECUTE_DIRECT
    START_AUTOSAMPLE = DriverEvent.START_AUTOSAMPLE
    STOP_AUTOSAMPLE = DriverEvent.STOP_AUTOSAMPLE
    CLOCK_SYNC = DriverEvent.CLOCK_SYNC
    WAIT_FOR_STATE = 'PROTOCOL_EVENT_WAIT_FOR_STATE'
    INIT_PARAMS = 'PROTOCOL_EVENT_INIT_PARAMS'
    
class Capability(BaseEnum):
    """
    Protocol events that should be exposed to users (subset of above).
    """
    GET = ProtocolEvent.GET
    SET = ProtocolEvent.SET
    START_AUTOSAMPLE = ProtocolEvent.START_AUTOSAMPLE
    STOP_AUTOSAMPLE = ProtocolEvent.STOP_AUTOSAMPLE
    START_DIRECT = DriverEvent.START_DIRECT
    STOP_DIRECT = DriverEvent.STOP_DIRECT
    EXECUTE_DIRECT = DriverEvent.EXECUTE_DIRECT
    CLOCK_SYNC = ProtocolEvent.CLOCK_SYNC
    
class AutoSampleMode(BaseEnum):
    """
    Enum for auto sampling mode, the numbers match with the options you can
    select from the menu.
    """
    NO_AUTO_SAMPLE = 'NO_AUTO_SAMPLE'
    HALF_HR_SAMPLE = 'HALF_HR_SAMPLE'
    ONE_HR_SAMPLE = 'ONE_HR_SAMPLE'
    THREE_HR_SAMPLE = 'THREE_HR_SAMPLE'
    SIX_HR_SAMPLE = 'SIX_HR_SAMPLE'
    TWELVE_HR_SAMPLE = 'TWELVE_HR_SAMPLE'
    DAILY_SAMPLE = 'DAILY_SAMPLE'
    CONTINUOUS_SAMPLE = 'CONTINUOUS_SAMPLE'
    
AUTO_SAMPLE_STR = {
    'None': 'NO_AUTO_SAMPLE',
    '30 Minute': 'HALF_HR_SAMPLE',
    'One Hour': 'ONE_HR_SAMPLE',
    'Three Hour': 'THREE_HR_SAMPLE',
    'Six Hour': 'SIX_HR_SAMPLE',
    'Twelve Hour': 'TWELVE_HR_SAMPLE',
    'Daily': 'DAILY_SAMPLE',
    'Continuous': 'CONTINUOUS_SAMPLE'
}

AUTO_SAMPLE_MENU_OPTS = {
    'NO_AUTO_SAMPLE': '0',
    'HALF_HR_SAMPLE': '1',
    'ONE_HR_SAMPLE': '2',
    'THREE_HR_SAMPLE': '3',
    'SIX_HR_SAMPLE': '4',
    'TWELVE_HR_SAMPLE': '5',
    'DAILY_SAMPLE': '6',
    'CONTINUOUS_SAMPLE': '7'
}
                            

class Parameter(DriverParameter):
    """
    Device specific parameters.
    """
    NUMBER_SAMPLES="number_samples"  
    MENU_WAIT_TIME="menu_wait_time"
    AUTO_SAMPLE_MODE="auto_sample_mode"
    ATMOSPHERE_MODE="atmosphere_mode"

class Command(BaseEnum):
    """
    Menu navigation commands
    """
    BACK_MENU = 'BACK_MENU'
    SPACE = 'SPACE'
    SET_CLOCK = 'SET_CLOCK'
    CHANGE_PARAM = 'CHANGE_PARAM'
    CHANGE_AUTO_START_MODE = 'CHANGE_AUTO_START_MODE'
    START_AUTOSAMPLE = 'START_AUTOSAMPLE'
    CHANGE_NUMBER_SAMPLES = 'CHANGE_NUMBER_SAMPLES'
    CHANGE_ATMOSPHERE_MODE = 'CHANGE_ATMOSPHERE_MODE'
    SET_MENU_WAIT_TIME = 'SET_MENU_WAIT_TIME'
    CONFIRM_SET_TIME = 'CONFIRM_SET_TIME'
    SET_YEAR = 'SET_YEAR'
    SET_MONTH = 'SET_MONTH'
    SET_DAY = 'SET_DAY'
    SET_HOUR = 'SET_HOUR'
    SET_MINUTE = 'SET_MINUTE'
    
# lines up with Command    
COMMAND_CHAR = {
    'BACK_MENU': '0',
    'SPACE': chr(0x20),
    'SET_CLOCK': '4',
    'CHANGE_PARAM': '5',
    'CHANGE_AUTO_START_MODE': '1',
    'START_AUTOSAMPLE': '7',
    'CHANGE_NUMBER_SAMPLES': '2',
    'CHANGE_ATMOSPHERE_MODE': '4',
    'SET_MENU_WAIT_TIME': '6',
    'CONFIRM_SET_TIME': 'Y'
}
    
class SubMenu(BaseEnum):
    """
    Sub menus
    """
    MAIN = 'SUBMENU_MAIN'
    SET_CLOCK = 'SUBMENU_SET_CLOCK'
    CHANGE_PARAM = 'SUBMENU_CHANGE_PARAM'
    CHANGE_AUTO_START_MODE = 'SUBMENU_CHANGE_AUTO_START_MODE'
    START_AUTOSAMPLE = 'SUBMENU_START_AUTOSAMPLE'
    STOP_AUTOSAMPLE = 'SUBMENU_STOP_AUTOSAMPLE'
    CHANGE_NUMBER_SAMPLES = 'SUBMENU_CHANGE_NUMBER_SAMPLES' 
    CHANGE_ATMOSPHERE_MODE = 'SUBMENU_CHANGE_ATMOSPHERE_MODE'
    SET_MENU_WAIT_TIME = 'SUBMENU_SET_MENU_WAIT_TIME'
     
class Prompt(BaseEnum):
    """
    Device i/o prompts and menus
    """
    MAIN_MENU = "1) Record Data Now           5) Auto Start Settings\r\n2) View Logged Data          6) Atmosphere Settings\r\n3) Erase Logged Data         7) Sleep Now\r\n4) Change Clock Time         8) View Live Data\r\n\r\n\r\nEnter Command >" 
    AUTO_START_MENU="1) Change Auto Start Program\r\n2) Change Number of Samples\r\n3) Change Re-Zero Interval\r\n4) Change Sampling Mode\r\n5) Reset Zero Count\r\n6) Change Menu Timer\r\n0) Return to Main Menu\r\n\r\nEnter Command >"
    AUTO_START_PROMPT= "Please select one of the following modes to autostart the logger with\r\n\r\n0. No Auto Start\r\n1. 30 Minute Auto Start\r\n2. 1 Hour Auto start\r\n3. 3 Hour Auto Start\r\n4. 6 Hour Auto Start\r\n5. 12 Hour Auto Start\r\n6. Daily Auto Start\r\n7. Continuous (Always Logging)\r\n\r\n>"
    CHANGE_NUMBER_SAMPLES="Please enter the amount of samples to take each time unit is automatically started [1-9]\r\n\r\n>" 
    MENU_WAIT_TIME="Please enter the number of seconds to wait for Menu to timeout without input [###]\r\n\r\n>"
    CHANGE_ATMOSPHERE="Please select from the following sample modes\r\n\r\n0 - Water Mode\r\n1 - Atomosphere Mode\r\n2 - Both Water & Atomosphere Mode\r\n\r\n>" 
    SET_TIME_CONFIRM="Would you still like to change the time? (Y/N)"
    SET_YEAR="Enter Year [####]"
    SET_MONTH="Enter Month [##]"
    SET_DAY="Enter Day [##]"
    SET_HOUR="Enter Hour [##]"
    SET_MINUTE="Enter Minute [##]"
    
MENU_PROMPTS = [Prompt.MAIN_MENU, Prompt.AUTO_START_MENU]

MENU = MenuInstrumentProtocol.MenuTree({
    SubMenu.MAIN:[],
    SubMenu.CHANGE_PARAM:[Directions(command=Command.CHANGE_PARAM,
                          response=Prompt.AUTO_START_MENU)],
})
      
   
    
###############################################################################
# Matchers - Data particles and prompts
###############################################################################
AIR_REGEX = r'#(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}), (M),(\d{5}),(\d{5}),(\d{3}.\d),(\d{2}.\d),(\d{2}.\d{3}),(\d{2}.\d{3}),(\d{4}),(\d{2}.\d),(\d{2}.\d),A'
AIR_REGEX_MATCHER = re.compile(AIR_REGEX)

WATER_REGEX = r'#(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}), (M),(\d{5}),(\d{5}),(\d{3}.\d),(\d{2}.\d),(\d{2}.\d{3}),(\d{2}.\d{3}),(\d{4}),(\d{2}.\d),(\d{2}.\d),W'
WATER_REGEX_MATCHER = re.compile(WATER_REGEX)

ESCAPE_AUTO_START_REGEX = r"Press Space-bar to escape Auto-Start \( \d+ Seconds \)...."
ESCAPE_AUTO_START_MATCHER = re.compile(ESCAPE_AUTO_START_REGEX)
# Half Hour Program, 3 Hour Program, Daily Program, Continuous Logging, No AutoStart Set
AUTO_START_REGEX = r"Next Start Time = \d\d:\d\d\r\n|Loading User Variables...\r\n|Detector Flow On\r|Detector Warmup\r|ATM & Water Mode Warmup\r|P0\r|P1\r"
AUTO_START_MATCHER = re.compile(AUTO_START_REGEX)

# Sync time response regexes
ENTER_MONTH_REGEX = r"Year = (\d+)\r\rEnter Month \[##\]"
ENTER_MONTH_MATCHER = re.compile(ENTER_MONTH_REGEX)
ENTER_DAY_REGEX = r"Month = (\d+)\r\rEnter Day \[##\]"
ENTER_DAY_MATCHER = re.compile(ENTER_DAY_REGEX)
ENTER_HOUR_REGEX = r"Day = (\d+)\r\rEnter Hour \[##\]"
ENTER_HOUR_MATCHER = re.compile(ENTER_HOUR_REGEX)
ENTER_MINUTE_REGEX = r"Hour = (\d+)\r\rEnter Minute \[##\]"
ENTER_MINUTE_MATCHER = re.compile(ENTER_MINUTE_REGEX)
MINUTE_RESP_MATCHER = re.compile("2\) View Logged Data          6\) Atmosphere Settings\r\n3\) Erase Logged Data         7\) Sleep Now\r\n4\) Change Clock Time         8\) View Live Data\r\n\r\n\r\nEnter Command >|" + ESCAPE_AUTO_START_REGEX)

class Pco2aAirSampleDataParticleKey(BaseEnum):
    """
    Data particle key for air sample
    """
    DATE_TIME_STRING = "date_time_string"
    BEGIN_MEASUREMENT = "begin_measurement"
    ZERO_A2D = "zero_a2d"
    CURRENT_A2D = "current_a2d"
    MEASURED_AIR_CO2 = "measured_air_co2"
    AVG_IRG_TEMPERATURE = "avg_irga_temperature"
    HUMIDITY = "humidity"
    HUMIDITY_TEMPERATURE = "humidity_temperature"
    GAS_STREAM_PRESSURE = "gas_stream_pressure"
    IRGA_DETECTOR_TEMPERATURE = "irga_detector_temperature"
    IRGA_SOURCE_TEMPERATURE = "irga_source_temperature"
    
class Pco2aAirSampleDataParticle(DataParticle):
    """
    Routines for parsing raw data into an air sample data particle structure.
    
    @throw SampleException If there is a problem with sample creation 
    """
    _data_particle_type = DataParticleType.PCO2A_AIR_SAMPLES

    def _build_parsed_values(self):
        """
        Parse air sample values from raw data into a dictionary
        @retval a dictionary of water data particles
        @raise SampleException if data does not actually match regex
        """
               
        matched = AIR_REGEX_MATCHER.match(self.raw_data)
        if not matched:
            raise SampleException("No regex match of parsed sample data: [%s]" %
                                  self.decoded_raw)
        
        particle_keys = [Pco2aAirSampleDataParticleKey.DATE_TIME_STRING,
                Pco2aAirSampleDataParticleKey.BEGIN_MEASUREMENT,
                Pco2aAirSampleDataParticleKey.ZERO_A2D,
                Pco2aAirSampleDataParticleKey.CURRENT_A2D,
                Pco2aAirSampleDataParticleKey.MEASURED_AIR_CO2,
                Pco2aAirSampleDataParticleKey.AVG_IRG_TEMPERATURE,
                Pco2aAirSampleDataParticleKey.HUMIDITY,
                Pco2aAirSampleDataParticleKey.HUMIDITY_TEMPERATURE,
                Pco2aAirSampleDataParticleKey.GAS_STREAM_PRESSURE,
                Pco2aAirSampleDataParticleKey.IRGA_DETECTOR_TEMPERATURE,
                Pco2aAirSampleDataParticleKey.IRGA_SOURCE_TEMPERATURE]
                        
        result = []
        index = 1
        for key in particle_keys:
            if key in [Pco2aAirSampleDataParticleKey.DATE_TIME_STRING,
                       Pco2aAirSampleDataParticleKey.BEGIN_MEASUREMENT]:
                result.append({DataParticleKey.VALUE_ID: key,
                                DataParticleKey.VALUE: matched.group(index)})
            elif key in [Pco2aAirSampleDataParticleKey.ZERO_A2D,
                                Pco2aAirSampleDataParticleKey.CURRENT_A2D,
                                Pco2aAirSampleDataParticleKey.GAS_STREAM_PRESSURE]:
                result.append({DataParticleKey.VALUE_ID: key,
                                DataParticleKey.VALUE: int(matched.group(index))})
            else:
                result.append({DataParticleKey.VALUE_ID: key,
                           DataParticleKey.VALUE: float(matched.group(index))})
            index += 1

        return result       
    
class Pco2aWaterSampleDataParticleKey(BaseEnum):
    """
    Data particle key for water sample
    """
    DATE_TIME_STRING = "date_time_string"
    BEGIN_MEASUREMENT = "begin_measurement"
    ZERO_A2D = "zero_a2d"
    CURRENT_A2D = "current_a2d"
    MEASURED_WATER_CO2 = "measured_water_co2"
    AVG_IRG_TEMPERATURE = "avg_irga_temperature"
    HUMIDITY = "humidity"
    HUMIDITY_TEMPERATURE = "humidity_temperature"
    GAS_STREAM_PRESSURE = "gas_stream_pressure"
    IRGA_DETECTOR_TEMPERATURE = "irga_detector_temperature"
    IRGA_SOURCE_TEMPERATURE = "irga_source_temperature" 
    
class Pco2aWaterSampleDataParticle(DataParticle):
    """
    Routines for passing raw data into a water sample data particle structure
    """
    _data_particle_type = DataParticleType.PCO2A_WATER_SAMPLES
    
    def _build_parsed_values(self):
        """
        Parse water sample values from raw data into a dictionary
        @retval a dictionary of water data particles
        @raise SampleException if data does not actually match regex
        """
        matched = WATER_REGEX_MATCHER.match(self.raw_data)
        if not matched:
            raise SampleException("No regex match of parsed sample data: [%s]" %
                                  self.decoded_raw)
        
        particle_keys = [Pco2aWaterSampleDataParticleKey.DATE_TIME_STRING,
                Pco2aWaterSampleDataParticleKey.BEGIN_MEASUREMENT,
                Pco2aWaterSampleDataParticleKey.ZERO_A2D,
                Pco2aWaterSampleDataParticleKey.CURRENT_A2D,
                Pco2aWaterSampleDataParticleKey.MEASURED_WATER_CO2,
                Pco2aWaterSampleDataParticleKey.AVG_IRG_TEMPERATURE,
                Pco2aWaterSampleDataParticleKey.HUMIDITY,
                Pco2aWaterSampleDataParticleKey.HUMIDITY_TEMPERATURE,
                Pco2aWaterSampleDataParticleKey.GAS_STREAM_PRESSURE,
                Pco2aWaterSampleDataParticleKey.IRGA_DETECTOR_TEMPERATURE,
                Pco2aWaterSampleDataParticleKey.IRGA_SOURCE_TEMPERATURE]      
            
        result = []
        index = 1
        for key in particle_keys:
            if key in [Pco2aWaterSampleDataParticleKey.DATE_TIME_STRING,
                       Pco2aWaterSampleDataParticleKey.BEGIN_MEASUREMENT]:
                result.append({DataParticleKey.VALUE_ID: key,
                               DataParticleKey.VALUE: matched.group(index)})
            elif key in [Pco2aWaterSampleDataParticleKey.ZERO_A2D,
                            Pco2aWaterSampleDataParticleKey.CURRENT_A2D,
                            Pco2aWaterSampleDataParticleKey.GAS_STREAM_PRESSURE]:
                result.append({DataParticleKey.VALUE_ID: key,
                               DataParticleKey.VALUE: int(matched.group(index))})
            else:
                result.append({DataParticleKey.VALUE_ID: key,
                               DataParticleKey.VALUE: float(matched.group(index))})
            index += 1

        return result  
    

###############################################################################
# Driver
###############################################################################

class Pco2aInstrumentDriver(SingleConnectionInstrumentDriver):
    """
    InstrumentDriver subclass
    Subclasses SingleConnectionInstrumentDriver with connection state
    machine.
    """
    def __init__(self, evt_callback):
        """
        Driver constructor.
        @param evt_callback Driver process event callback.
        """
        #Construct superclass.
        SingleConnectionInstrumentDriver.__init__(self, evt_callback)

    ########################################################################
    # Superclass overrides for resource query.
    ########################################################################

    def get_resource_params(self):
        """
        Return list of device parameters available.
        """
        return Parameter.list()

    ########################################################################
    # Protocol builder.
    ########################################################################

    def _build_protocol(self):
        """
        Construct the driver protocol state machine.
        """
        self._protocol = Protocol(MENU, Prompt, NEWLINE, self._driver_event) 
        
###########################################################################
# Protocol
###########################################################################

class Protocol(MenuInstrumentProtocol):
    """
    Instrument protocol class
    Subclasses MenuInstrumentProtocol
    """
    def __init__(self, menu, prompts, newline, driver_event):
        """
        Protocol constructor.
        @param menu 
        @param prompts A BaseEnum class containing instrument prompts.
        @param newline The newline.
        @param driver_event Driver process event callback.
        """
        # Construct protocol superclass.
        MenuInstrumentProtocol.__init__(self, menu, prompts, newline, driver_event)
        
        # Build protocol state machine.
        self._protocol_fsm = ThreadSafeFSM(ProtocolState, ProtocolEvent,
                            ProtocolEvent.ENTER, ProtocolEvent.EXIT)

        # Add event handlers for protocol state machine.
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.ENTER, self._handler_unknown_enter)
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.DISCOVER, self._handler_unknown_discover)
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.INIT_PARAMS, self._handler_command_init)
    
        self._protocol_fsm.add_handler(ProtocolState.DISCOVERY, ProtocolEvent.ENTER, self._handler_discovery_enter)
        self._protocol_fsm.add_handler(ProtocolState.DISCOVERY, ProtocolEvent.WAIT_FOR_STATE, self._handler_discovery_wait_for_state)
        self._protocol_fsm.add_handler(ProtocolState.DISCOVERY, ProtocolEvent.START_DIRECT, self._handler_command_start_direct)
        self._protocol_fsm.add_handler(ProtocolState.DISCOVERY, ProtocolEvent.INIT_PARAMS, self._handler_command_init)
               
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.ENTER, self._handler_command_enter)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_DIRECT, self._handler_command_start_direct)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.GET, self._handler_command_get)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.SET, self._handler_command_set)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.CLOCK_SYNC, self._handler_command_clock_sync)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_AUTOSAMPLE, self._handler_command_autosample)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.INIT_PARAMS, self._handler_command_init)
                                       
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.ENTER, self._handler_autosample_enter)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.STOP_AUTOSAMPLE, self._handler_autosample_exit)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.INIT_PARAMS, self._handler_autosample_init)
        
        self._protocol_fsm.add_handler(ProtocolState.WAIT_FOR_COMMAND, ProtocolEvent.ENTER, self._handler_wait_for_command_enter)
        self._protocol_fsm.add_handler(ProtocolState.WAIT_FOR_COMMAND, ProtocolEvent.WAIT_FOR_STATE, self._handler_wait_for_command_wait_for_state)
        self._protocol_fsm.add_handler(ProtocolState.WAIT_FOR_COMMAND, ProtocolEvent.INIT_PARAMS, self._handler_command_init)
        
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.ENTER, self._handler_direct_access_enter)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.EXIT, self._handler_direct_access_exit)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.STOP_DIRECT, self._handler_direct_access_stop_direct)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.EXECUTE_DIRECT, self._handler_direct_access_execute_direct)
        
        log.trace("Created Protocol state machine")
        
        # Construct the parameter dictionary containing device parameters,
        # current parameter values, and set formatting functions.
        self._build_driver_dict()
        self._build_param_dict()
        self._build_command_dict()

        # Add build handlers for device commands.
        self._add_build_handler(Command.BACK_MENU, self._build_menu_command)
        self._add_build_handler(Command.SPACE, self._build_menu_command)
        self._add_build_handler(Command.CHANGE_PARAM, self._build_menu_command)
        self._add_build_handler(Command.START_AUTOSAMPLE, self._build_menu_command)
        self._add_build_handler(Command.CHANGE_AUTO_START_MODE, self._build_menu_input_command)
        self._add_build_handler(Command.CHANGE_NUMBER_SAMPLES, self._build_menu_input_command)
        self._add_build_handler(Command.SET_CLOCK, self._build_menu_command)
        self._add_build_handler(Command.CONFIRM_SET_TIME, self._build_menu_command)
        self._add_build_handler(Command.SET_MENU_WAIT_TIME, self._build_menu_input_command)
        self._add_build_handler(Command.CHANGE_ATMOSPHERE_MODE, self._build_menu_input_command)
        self._add_build_handler(Command.SET_YEAR, self._build_input)
        self._add_build_handler(Command.SET_MONTH, self._build_input)
        self._add_build_handler(Command.SET_DAY, self._build_input)
        self._add_build_handler(Command.SET_HOUR, self._build_input)
        self._add_build_handler(Command.SET_MINUTE, self._build_input)
        
        # Add response handlers for device commands.
        self._add_response_handler(Command.BACK_MENU, self._parse_menu_change_response)
        self._add_response_handler(Command.SPACE, self._parse_menu_change_response)
        self._add_response_handler(Command.CHANGE_PARAM, self._parse_show_param_response)
        self._add_response_handler(Command.CHANGE_AUTO_START_MODE, self._parse_menu_change_response)
        self._add_response_handler(Command.CHANGE_NUMBER_SAMPLES, self._parse_menu_change_response)
        self._add_response_handler(Command.SET_CLOCK, self._parse_menu_change_response)
        self._add_response_handler(Command.CONFIRM_SET_TIME, self._parse_menu_change_response)
        self._add_response_handler(Command.SET_MENU_WAIT_TIME, self._parse_menu_change_response)
        self._add_response_handler(Command.CHANGE_ATMOSPHERE_MODE, self._parse_menu_change_response)
        self._add_response_handler(Command.SET_YEAR, self._parse_time_regex_response)
        self._add_response_handler(Command.SET_MONTH, self._parse_time_regex_response)
        self._add_response_handler(Command.SET_DAY, self._parse_time_regex_response)
        self._add_response_handler(Command.SET_HOUR, self._parse_time_regex_response)
        self._add_response_handler(Command.SET_MINUTE, self._parse_menu_change_response)
        

        # Add sample handlers.

        # State state machine in UNKNOWN state.
        self._protocol_fsm.start(ProtocolState.UNKNOWN)

        # commands sent sent to device to be filtered in responses for telnet DA
        self._sent_cmds = []

        #
        self._chunker = StringChunker(Protocol.sieve_function)


    @staticmethod
    def sieve_function(raw_data):
        """
        The method that splits samples
        @param raw_data - incoming raw data
        @retval A list of matched raw data
        """

        return_list = []

        sieve_matchers = [ AIR_REGEX_MATCHER,
                           WATER_REGEX_MATCHER]

        for matcher in sieve_matchers:
            for match in matcher.finditer(raw_data):
                return_list.append((match.start(), match.end()))

        return return_list

    def _build_param_dict(self):
        """
        Populate the parameter dictionary with parameters.
        For each parameter key, add match stirng, match lambda function,
        and value formatting function for set commands.
        """
        # Add parameter handlers to parameter dict.
        self._param_dict = ProtocolParameterDict()
        
        # Add parameter handlers to parameter dict.
        self._param_dict.add(Parameter.NUMBER_SAMPLES,
                             r'Number of Samples: +(\d+)\r',
                             lambda match : int(match.group(1)),
                             self._int_to_string,
                             type=ParameterDictType.INT,
                             startup_param=True,
                             direct_access=False,
                             init_value=5,
                             menu_path_read=SubMenu.CHANGE_PARAM,
                             submenu_read=[],
                             menu_path_write=SubMenu.CHANGE_PARAM,
                             submenu_write=[["2", Prompt.CHANGE_NUMBER_SAMPLES]],
                             display_name="number samples"
        )
        self._param_dict.add(Parameter.MENU_WAIT_TIME,
                             r'Menu Timeout: +(\d+)\r',
                             lambda match : int(match.group(1)),
                             self._int_to_string,
                             type=ParameterDictType.INT,
                             startup_param=True,
                             direct_access=False,
                             init_value=20,
                             menu_path_read=SubMenu.CHANGE_PARAM,
                             submenu_read=[],
                             menu_path_write=SubMenu.CHANGE_PARAM,
                             submenu_write=[["6", Prompt.MENU_WAIT_TIME]],
                             display_name="menu wait time"
        )
        self._param_dict.add(Parameter.AUTO_SAMPLE_MODE,
                             r'Auto Start Program: (30 Minute|[a-zA-Z]+ Hour|[a-zA-Z]+)\r',
                             lambda match : self._to_autosample(match.group(1)),
                             str,
                             type=ParameterDictType.STRING,
                             startup_param=True,
                             direct_access=False,
                             init_value=AutoSampleMode.ONE_HR_SAMPLE,
                             menu_path_read=SubMenu.CHANGE_PARAM,
                             submenu_read=[],
                             menu_path_write=SubMenu.CHANGE_AUTO_START_MODE,
                             submenu_write=[["1", Prompt.AUTO_START_PROMPT]],
                             display_name="auto sample mode"                             
        )
        self._param_dict.add(Parameter.ATMOSPHERE_MODE,
                             r'Atmosphere Mode: +(\d)\r',
                             lambda match : int(match.group(1)),
                             self._int_to_string,
                             type=ParameterDictType.INT,
                             startup_param=True,
                             direct_access=False,
                             init_value=2,
                             menu_path_read=SubMenu.CHANGE_PARAM,
                             submenu_write=[["4", Prompt.CHANGE_ATMOSPHERE]],
                             display_name="atmosphere mode"
        )
                             
        
    def _build_command_dict(self):
        """
        Populate the command dictionary with command.
        """
        self._cmd_dict.add(Capability.CLOCK_SYNC, display_name="sync clock")
        self._cmd_dict.add(Capability.START_AUTOSAMPLE, display_name="start autosample")
        self._cmd_dict.add(Capability.STOP_AUTOSAMPLE, display_name="stop autosample")
        
    def _build_driver_dict(self):
        """
        Populate the driver dictionary with options
        """
        self._driver_dict.add(DriverDictKey.VENDOR_SW_COMPATIBLE, True)

    def _got_chunk(self, chunk, timestamp):
        """
        The base class got_data has gotten a chunk from the chunker.  Pass it to extract_sample
        with the appropriate particle objects and REGEXes.
        """
        self._extract_sample(Pco2aAirSampleDataParticle, AIR_REGEX_MATCHER, chunk, timestamp)
        self._extract_sample(Pco2aWaterSampleDataParticle, WATER_REGEX_MATCHER, chunk, timestamp)
        log.info("Got chunk: %s", chunk)
                   
    def _filter_capabilities(self, events):
        """
        Return a list of currently available capabilities.
        """
        return [x for x in events if Capability.has(x)]
    
    ########################################################################
    # Command builders
    ########################################################################
    def _build_menu_command(self, cmd, *args):
        """
        Pick the right command character
        @retval Text string of the command character for the input command
        @raises InstrumentProtocolException if a command with an unknown
        corresponding character is input
        """
        if COMMAND_CHAR[cmd]:
            return COMMAND_CHAR[cmd]
        else:
            raise InstrumentProtocolException("Unknown command character for %s" % cmd)
           
    def _build_menu_input_command(self, cmd, arg):
        """
        Combine character to pick menu and the input after the menu is selected
        @retval A string combining the command character and arguments to follow
        @raises InstrumentProtocolException if a command with an unknown
        corresponding character is input
        """
        if COMMAND_CHAR[cmd]:
            return "%s%s" % (COMMAND_CHAR[cmd], arg)
        else:
            raise InstrumentProtocolException("Unknown command character for %s" % cmd)
        
    def _build_input(self, cmd, arg):
        """
        Simply return the argument as a string, no command char is needed just direct
        input, so ignore the command.  Used for setting the time fields (year,
        month, day, hour, minute).
        @retval A string of the input argument
        """
        return "%s" % arg
       
    ########################################################################
    # Command parsers
    ########################################################################
    def _parse_menu_change_response(self, response, prompt):
        """
        Parse a response to a menu change
        
        @param response What was sent back from the command that was sent
        @param prompt The prompt that was returned from the device
        @retval The prompt that was encountered after the change
        """
        log.info("Parsing menu change response with prompt: %s", prompt)
        return prompt
    
    def _parse_show_param_response(self, response, prompt):
        """
        Parse the show parameter response screen
        
        @param response Response that was sent back from the command
        @param prompt The prompt that was returned from the device
        """
        log.info("Parsing show parameter screen")
        self._param_dict.update_many(response)
        
    def _parse_time_regex_response(self, response, prompt):
        """
        Parse one of the result prompts from setting one of the time fields.
        These will contain the value that the instrument received for that
        field.  Return the matched value so error checking can be done. 
        """
        log.info("Parsing time regex response %s", response)
        return response
    
    
    ########################################################################
    # Utilities
    ########################################################################
    def _go_to_root_menu(self):
        """
        Get back to the root menu, assuming we are in COMMAND mode.
        Getting to command mode should be done before this method is called.
        @raises InstrumentProtocolException if cannot get to root menu
        """
        log.debug("Going to root menu.")
        
        # BACK MENU will re-display the main menu, or bring you back from 
        # the auto start menu
        # loop 5 times trying to get back to main menu
        response = ''
        index = 0;
        while not str(response).lstrip().endswith(Prompt.MAIN_MENU):
            try:
                response = self._do_cmd_resp(Command.BACK_MENU)
            except InstrumentTimeoutException:
                if index > 4:
                    raise InstrumentProtocolException("Not able to get valid command prompt. "
                                                        "Is instrument in command mode?")
            index += 1
            if not str(response).lstrip().endswith(Prompt.MAIN_MENU):
                time.sleep(1)
               
    def _update_params(self):
        """
        Fetch the parameters from the device, and update the param dict.
        
        @raises InstrumentProtocolException
        @raises InstrumentTimeoutException
        """
        log.info("Updating parameter dict")
        old_config = self._param_dict.get_config()
        # Navigate to parameter screen...the parser for the command
        # does the update_many(), updating the param dict
        self._go_to_root_menu()
        self._navigate(SubMenu.CHANGE_PARAM)
        self._go_to_root_menu()
        new_config = self._param_dict.get_config()            
        if (new_config != old_config):
            self._driver_event(DriverAsyncEvent.CONFIG_CHANGE)
            # Added this sleep to make sure the async config change takes affect 
            #time.sleep(10)
        log.info("Done updating parameter dict")
                       
    def _in_command_mode(self, numberTries=1):
        """
        Determine if we are in command mode or not by sending a space character to
        the instrument.  If it is in command mode, a prompt will be returned, otherwise
        it will be unresponsive and not in command mode.
        @param numberTries - The number of times to try to get the instrument to respond to a space
        @retval - returns True if we are in command mode, False if not
        """
        # loop sending space chars for numberTries
        for i in range(0,numberTries):
            try:
                response = self._do_cmd_resp(Command.SPACE, timeout=10,
                                 expected_prompt=[Prompt.MAIN_MENU, Prompt.AUTO_START_MENU])
                log.debug("Found command mode")
                return True
            except InstrumentTimeoutException:
                log.debug("Timeout waiting for instrument response")
                
        return False
    
    def _set_params(self, *args, **kwargs):
        """
        Set the value of each of the parameters input in the params argument.
        @param params - dictionary of parameters to set
        @raise InstrumentParameterException - if an invalid parameter is set
        @raise InstrumentProtocolException - if there is an error setting the parameter
        """
        log.debug("In _set_params")
        startup = False
        try:
            params = args[0]
        except IndexError:
            raise InstrumentParameterException('Set command requires a parameter dict.')

        try:
            startup = args[1]
        except IndexError:
            pass
        log.info("Setting parameters: %s", params)
        self._verify_not_readonly(*args, **kwargs)
        
        self._go_to_root_menu()
        # all parameters are read/write and are in the auto start menu, we can
        # just stay in that menu for setting each parameter
        self._navigate(SubMenu.CHANGE_PARAM)

        for (key, value) in params.iteritems():
            if not Parameter.has(key):
                raise InstrumentParameterException()

            try:
                if (key == Parameter.AUTO_SAMPLE_MODE):                             
                    self._do_cmd_resp(Command.CHANGE_AUTO_START_MODE,
                                      self._from_autosample(value),
                                      expected_prompt=[Prompt.AUTO_START_MENU],
                                      write_delay=1, **kwargs)
                elif (key == Parameter.MENU_WAIT_TIME):            
                    self._do_cmd_resp(Command.SET_MENU_WAIT_TIME, self._to_menu_wait_time(value),
                                      expected_prompt=[Prompt.AUTO_START_MENU],
                                      write_delay=1, **kwargs)
                elif (key == Parameter.NUMBER_SAMPLES):
                    if not isinstance(value, int) or value > 9 or value < 1:
                        raise InstrumentParameterException(
                            'Number of samples %s is not an integer or outside 1-9' % value)
                    self._do_cmd_resp(Command.CHANGE_NUMBER_SAMPLES, value,
                                      expected_prompt=[Prompt.AUTO_START_MENU],
                                      write_delay=1, **kwargs)
                elif (key == Parameter.ATMOSPHERE_MODE):
                    if not isinstance(value, int) or value > 2 or value < 0:
                        raise InstrumentParameterException(
                            'Atmosphere mode %s is not an integer or outside 0-2' % value)
                    self._do_cmd_resp(Command.CHANGE_ATMOSPHERE_MODE, value,
                                      expected_prompt=[Prompt.AUTO_START_MENU],
                                      write_delay=1, **kwargs)
            except InstrumentProtocolException:
                self._go_to_root_menu()
                raise InstrumentProtocolException("Could not set parameter %s"  % key)
            
        self._update_params()
                
    def _sync_clock(self, zero_year=False):
        """
        Sync the clock to the current time.
        @param cmd_timeout - optional timeout on commands
        @param time_format - optional time format string 
        @raises InstrumentTimeoutException - if the response did not occur in time.
        @raises InstrumentProtocolException - if any command could not be 
        built or if response was not recognized.
        """
        # there needs to be a time delay in between sending each character,
        # otherwise chars get missed
        char_delay = 1
        # total delay added from time in between characters, 12 chars are sent total plus 
        # waits for the set and confirm commands
        total_delay = (char_delay * 12) + 2
        
        cmd_timeout=30
        
        # get the current time aligned to the closet minute, since we cannot set seconds,
        # with a delay of the seconds it will take to set the time
        time_format="%Y %m %d %H:%M:%S"
        str_val = get_timestamp_delayed(time_format, align='minute', offset=total_delay)
        log.info("Set time value == '%s' delayed %s", str_val, total_delay)
        success = True
        
        self._go_to_root_menu()
        response = self._do_cmd_resp(Command.SET_CLOCK,
                                     timeout=cmd_timeout,
                                     write_delay=1,
                                     expected_prompt=[Prompt.SET_TIME_CONFIRM])
        if response == Prompt.SET_TIME_CONFIRM:
            # make sure to delay after set time so first year char does not get lost
            response = self._do_cmd_resp(Command.CONFIRM_SET_TIME,
                                         timeout=cmd_timeout,
                                         write_delay=1,
                                         expected_prompt=[Prompt.SET_YEAR])
            if response == Prompt.SET_YEAR:
                # Sometimes the clock does not sync properly for unknown reasons,
                # and adds an extra digit to the end (i.e 20134 instead of 2013)
                # Setting the time with a last digit of 0 seems to clear this
                # extra digit so you can set the time again
                if zero_year:
                    log.info("Setting last year digit to zero")
                    year_str = str_val[0:3] + "0"
                else:
                    year_str = str_val[0:4]    
                response = self._do_cmd_resp(Command.SET_YEAR, year_str,
                                         timeout=cmd_timeout,
                                         write_delay=char_delay,
                                         response_regex=ENTER_MONTH_MATCHER)
                year_match = ENTER_MONTH_MATCHER.search(response)
                if (not year_match or year_match.group(1) != year_str):
                    log.debug("Year did not match")
                    success = False
                response = self._do_cmd_resp(Command.SET_MONTH, str_val[5:7],
                                          timeout=cmd_timeout,
                                          write_delay=char_delay,
                                          response_regex=ENTER_DAY_MATCHER)
                month_match = ENTER_DAY_MATCHER.search(response)
                if (not month_match or month_match.group(1) != str_val[5:7]):
                    log.debug("Month did not match")
                    success = False
                response = self._do_cmd_resp(Command.SET_DAY, str_val[8:10],
                                        timeout=cmd_timeout,
                                        write_delay=char_delay,
                                        response_regex=ENTER_HOUR_MATCHER)
                day_match = ENTER_HOUR_MATCHER.search(response)
                if (not day_match or day_match.group(1) != str_val[8:10]):
                    log.debug("Day did not match")
                    success = False
                response = self._do_cmd_resp(Command.SET_HOUR, str_val[11:13],
                                         timeout=cmd_timeout,
                                         write_delay=char_delay,
                                         response_regex=ENTER_MINUTE_MATCHER)
                hour_match = ENTER_MINUTE_MATCHER.search(response)
                if (not hour_match or hour_match.group(1) != str_val[11:13]):
                    log.debug("Hour did not match")
                    success = False
                # Try to match either the main menu or escape from auto start
                # text for the response
                response = self._do_cmd_resp(Command.SET_MINUTE, str_val[14:16],
                                            timeout=cmd_timeout,
                                            write_delay=char_delay,
                                            response_regex = MINUTE_RESP_MATCHER)
                                            #expected_prompt=[Prompt.MAIN_MENU])
                # I think due to timing, sometimes the clock sync just goes right
                # to 'sleep', which starts auto sample.  We don't want that to
                # happen, so send a space to stay in command                            
                escape_auto_match = ESCAPE_AUTO_START_MATCHER.search(response)
                if (escape_auto_match):
                    response = self._do_cmd_resp(Command.SPACE, 
                                            timeout=cmd_timeout,
                                            expected_prompt=[Prompt.MAIN_MENU])
            else:
                success = False
        else:
            success = False
        if success:
            log.debug("Setting clock successful")
        return success
                
                                        
    def _wakeup(self, timeout=None):
        """
        Override wakeup so that nothing happens.
        """
        pass
    
    def _new_event(self, protocol_event_type):
        """
        This function is used to start a new event in a new thread.  
        """
        self._protocol_fsm.on_event(protocol_event_type)
              

    ########################################################################
    # Unknown handlers.
    ########################################################################

    def _handler_unknown_enter(self, *args, **kwargs):
        """
        Enter unknown state
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
           
    def _handler_unknown_discover(self, *args, **kwargs):
        """
        Check if the instrument is responding to commands.  If it is,
        enter command state, otherwise go into discovery state.
        @retval (next_state, (next_agent_state, result))
        """
        next_state = None
        next_agent_state = None
        result = None
        
        log.debug("Had discover event")
        # loop multiple times trying to get in command mode in case
        # the instrument is booting up
        if self._in_command_mode(numberTries=2):
            next_state = ProtocolState.COMMAND
            next_agent_state = ResourceAgentState.IDLE
        else:
            next_state = ProtocolState.DISCOVERY
            next_agent_state = ResourceAgentState.BUSY
        return (next_state, next_agent_state)       
        
    
    ########################################################################
    # Discovery handlers
    ########################################################################
    def _handler_discovery_enter(self, *args, **kwargs):
        """
        Enter discover state.  Start a new thread which handles waiting for
        discovery text from the instrument to arrive.  
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        
        thread.start_new_thread(self._new_event, (ProtocolEvent.WAIT_FOR_STATE, ))
        
    def _handler_discovery_wait_for_state(self, *args, **kwargs):
        """
        Loop waiting for the instrument to output text which indicates what state it is in.
        @ raise InstrumentTimeoutException - if no state is found within timeout
        """
        next_state = None
        next_agent_state = None
        result = None
        
        matcher_set = [ AIR_REGEX_MATCHER,
                        WATER_REGEX_MATCHER,
                        ESCAPE_AUTO_START_MATCHER,
                        AUTO_START_MATCHER]
        
        log.info("waiting for command or autosample")
        # wait for two hours, this handles if we are at the default sample rate of one hour.
        # If the sample rate is longer than an hour, this timeout should be increased
        timeout = 7200
        starttime = time.time()
        while True:
            for matcher in matcher_set:
                found_match = matcher.search(self._promptbuf)
                if found_match:
                    if matcher == ESCAPE_AUTO_START_MATCHER:
                        log.info("Discovered escape from auto sample text")
                        self._do_cmd_resp(Command.SPACE, timeout=30, expected_prompt=Prompt.MAIN_MENU)
                        next_state = ProtocolState.COMMAND
                        next_agent_state = ResourceAgentState.IDLE
                    else:
                        log.info("Discovered auto sampling")
                        next_state = ProtocolState.AUTOSAMPLE
                        next_agent_state = ResourceAgentState.STREAMING
                    break
                else:
                    time.sleep(.5)
            if next_state is not None:
                break
            if time.time() > starttime + timeout:
                raise InstrumentTimeoutException("no state discovered in _wait_for_state()")
            
        log.debug("Next state is %s", next_state)    
        if next_state is not None:  
            self._async_agent_state_change(next_agent_state)
            log.debug('Should have async changed agent states')
        return (next_state, (next_agent_state, result))

    
    ########################################################################
    # Command handlers.
    ########################################################################

    def _handler_command_enter(self, *args, **kwargs):
        """
        Enter command state. Get an update of the parameters that are on the device. 
        @throws InstrumentTimeoutException if the device cannot be woken.
        @throws InstrumentProtocolException if the update commands and not recognized.
        """
        log.debug("Entering command state")
        # initializing params is different if coming from command or autosample
        self._protocol_fsm.on_event(ProtocolEvent.INIT_PARAMS)
        log.debug("Done with INIT_PARAMS event")
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        
    def _handler_command_init(self, *args, **kwargs):
        """
        Do initialization
        """
        next_state = None
        next_agent_state = None
        result = None
        
        # do initialization
        self._init_params()
        return (next_state, (next_agent_state, result))

    def _handler_command_get(self, *args, **kwargs):
        """
        Get parameters while in the command state.
        @param params List of the parameters to pass to the state
        @retval returns (next_state, (next_agent_state, result)) where result is a dict {}. 
        @throw InstrumentParameterException for invalid parameter
        """
        return self._handler_get(*args, **kwargs)
    
    def _handler_command_set(self, *args, **kwargs):
        """
        Handle setting data from command mode
         
        @param params Dict of the parameters and values to pass to the state
        @retval return (next state, result)
        @throw InstrumentProtocolException For invalid parameter
        """
        next_state = None
        result = None
        log.debug("In _handler_command_set")
                
        self._set_params(*args, **kwargs)
                   
        return (next_state, result)
    
    def _handler_command_clock_sync(self, *args, **kwargs):
        """
        Handle setting the clock.  The clock may not set correctly,
        and if it doesn't there is a corrective measure that can be
        taken to try to fix it.  If the clock gets set to a strange
        time, this will throw off when the instrument takes samples,
        (i.e. not for thousands of years if the year is off...)
        @retval (next_state, (next_agent_state, result))
        """
        next_state = None
        next_agent_state = None
        result = None
        
        success = self._sync_clock()
        
        timeout = 600
        starttime = time.time()
        while not success:
            log.debug("Failed to set clock, zeroing last digit")
            self._sync_clock(zero_year=True)
            log.debug("Syncing clock again")
            success = self._sync_clock()
            
            if time.time() > (starttime + timeout):
                raise InstrumentTimeoutException("could not sync clock successfully within 10 min")
                                
        return (next_state, (next_agent_state, result))
    
    def _handler_command_autosample(self, *args, **kwargs):
        """
        Start autosample mode
        @retval (next_state, (next_agent_state, result))
        """
        next_state = None
        next_agent_state = None
        result = None
        
        self._go_to_root_menu()
        self._do_cmd_no_resp(Command.START_AUTOSAMPLE)
        # sleep 20 seconds so that we get past the timeout where we can
        # escape from auto sampling
        log.debug("Sleeping 20 s to avoid escaping out of autosample")
        time.sleep(20)
        
        next_state = ProtocolState.AUTOSAMPLE
        next_agent_state = ResourceAgentState.STREAMING
        
        return (next_state, (next_agent_state, result))

    def _handler_command_start_direct(self, *args, **kwargs):
        """
        Start direct access
        @retval (next_state, (next_agent_state, result))
        """
        next_state = ProtocolState.DIRECT_ACCESS
        next_agent_state = ResourceAgentState.DIRECT_ACCESS
        result = None
        log.debug("_handler_command_start_direct: entering DA mode")
        return (next_state, (next_agent_state, result))
    
    ########################################################################
    # Autosample handlers
    ########################################################################
    
    def _handler_autosample_enter(self, *args, **kwargs):
        """
        Enter autosample mode
        """
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        
    def _handler_autosample_exit(self, *args, **kwargs):
        """
        Stop autosample mode - In modes other than continuous sampling, this
        requires waiting for the escape from auto sampling to happen.  In
        continuous mode, you can send a space to escape.
        @retval (next_state, (next_agent_state, result))
        """
        next_state = None
        next_agent_state = None
        result = None
        
        if self._in_command_mode():
            next_state = ProtocolState.COMMAND
            next_agent_state = ResourceAgentState.COMMAND
        else:
            next_state = ProtocolState.WAIT_FOR_COMMAND
            next_agent_state = ResourceAgentState.STREAMING
        
        return (next_state, (next_agent_state, result))
    
    def _handler_autosample_init(self, *args, **kwargs):
        """
        initialize parameters.  We need to put the instrument into
        command mode, apply the parameters, then put it back.
        """
        next_state = None
        next_agent_state = None
        result = None
        error = None
        
        log.info("Initializing params from autosample")
        try:
            if not self._in_command_mode():
                log.debug("waiting for escape from autosample text")
                timeout = 7200
                starttime = time.time()
                while True:
                    if ESCAPE_AUTO_START_MATCHER.search(self._promptbuf):
                        log.info("Discovered escape from auto sample text")
                        self._do_cmd_resp(Command.SPACE, timeout=30, expected_prompt=Prompt.MAIN_MENU)
                        break
                    else:
                        time.sleep(.1)

                    if time.time() > (starttime + timeout):
                        raise InstrumentTimeoutException("no state discovered in _handler_autosample_init()")
                
            self._init_params()
            log.info("Done with _init_params")
        # Catch all error so we can put ourself back into
        # streaming.  Then rethrow the error
        except Exception as e:
            error = e

        finally:
            self._go_to_root_menu()
            self._do_cmd_no_resp(Command.START_AUTOSAMPLE)

        if(error):
            log.error("Error initializing params from autosample: %s", error)
            raise error
        return (next_state, (next_agent_state, result))
    
    ########################################################################
    # Wait for command handlers
    ########################################################################
    def _handler_wait_for_command_enter(self, *args, **kwargs):
        """
        Enter wait for command state.
        Loop waiting for the escape from autosample text from the instrument.
        @raises InstrumentTimeoutException - if no state is found within the timeout period
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        
        thread.start_new_thread(self._new_event, (ProtocolEvent.WAIT_FOR_STATE, ))
        
    def _handler_wait_for_command_wait_for_state(self, *args, **kwargs):
        """
        Found the escape command mode trigger, go into command mode.
        @retval (next_state, (next_agent_state, result))
        """
        next_state = None
        next_agent_state = None
        result = None
        
        log.debug("In wait for command, waiting for escape from autosample text")
        timeout = 7200
        starttime = time.time()
        while True:
            if ESCAPE_AUTO_START_MATCHER.search(self._promptbuf):
                log.debug("Discovered escape from auto sample text")
                # send space to tell instrument to not start auto sampling
                self._do_cmd_resp(Command.SPACE, timeout=30, expected_prompt=Prompt.MAIN_MENU)
                next_state = ProtocolState.COMMAND
                next_agent_state = ResourceAgentState.COMMAND
                break
            else:
                time.sleep(.1)

            if time.time() > starttime + timeout:
                raise InstrumentTimeoutException("no state discovered in _handler_wait_for_command_wait_for_state()")
            
        log.debug("Returning state %s", next_state)    
        if next_state is not None:  
            self._async_agent_state_change(next_agent_state)
            log.info('Should have async changed agent states')
        return (next_state, (next_agent_state, result))
    
        
    ########################################################################
    # Direct access handlers.
    ########################################################################

    def _handler_direct_access_enter(self, *args, **kwargs):
        """
        Enter direct access state.
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

        self._sent_cmds = []

    def _handler_direct_access_exit(self, *args, **kwargs):
        """
        Exit direct access state.
        """
        pass

    def _handler_direct_access_execute_direct(self, data):
        """
        Direct access execute
        @retval (next_state, (next_agent_state, result))
        """
        next_state = None
        result = None
        next_agent_state = None

        self._do_cmd_direct(data)

        # add sent command to list for 'echo' filtering in callback
        self._sent_cmds.append(data)

        return (next_state, (next_agent_state, result))

    def _handler_direct_access_stop_direct(self):
        """
        Stop direct access, move to command state.
        @retval (next_state, (next_agent_state, result))
        """
        next_state = None
        result = None
        
        if self._in_command_mode():
            next_state = ProtocolState.COMMAND
            next_agent_state = ResourceAgentState.COMMAND
        else:
            next_state = ProtocolState.WAIT_FOR_COMMAND
            next_agent_state = ResourceAgentState.STREAMING

        return (next_state, (next_agent_state, result))
    
    ########################################################################
    # Static helpers.
    ########################################################################
    @staticmethod
    def _to_autosample(value):
        """
        Convert auto sampling string to auto sampling enum
        @param value - the auto sampling text string received from the instrument
        @retval - the auto sampling enum
        @raise InstrumentParameterException - if the input text is not a
        member of the AUTO_SAMPLE_STR
        """
        if not value in AUTO_SAMPLE_STR:
            raise InstrumentParameterException(
                'Value %s is not a member of the AUTO_SAMPLE_STR.' % value)
         
        return AUTO_SAMPLE_STR[value]
            

    @staticmethod
    def _from_autosample(value):
        """
        Converts from auto sampling mode enum to character to select auto sampling mode
        @param value - the auto sampling enum
        @retval - the auto sampling menu option number (as a char)
        @raise InstrumentParameterException - if the input text is not a
        member of the AUTO_SAMPLE_MENU_OPTS
        """
        if not value in AUTO_SAMPLE_MENU_OPTS:
            raise InstrumentParameterException(
                'Value %s is not in auto sample menu opts' % value)
            
        return AUTO_SAMPLE_MENU_OPTS[value]       

    @staticmethod
    def _to_menu_wait_time(value):
        """
        Convert from the numerical value to the set of characters (there must
        be 3 digits) for setting the menu wait time
        @param - integer to convert to 3 char menu wait time
        @retval - a menu wait time as 3 characters
        @raise InstrumentParameterException - if the value is not in allowed limits 
        """
        if not isinstance(value, int):
            raise InstrumentParameterException(
                'Menu wait time %s is not an integer' % value)
        
        if value <= 0 or value > 560:
            raise InstrumentParameterException(
                'Menu wait time must be 1-560 seconds, but attempted %s' % value)
        
        chars = "%s" % value
        nchars = len(chars)
        
        result = None;
        
        if nchars == 3:
            result = chars
        elif nchars == 2:
            result = '0' + chars
        elif nchars == 1:
            result = '00' + chars
        else:
            raise InstrumentParameterException(
                'Menu wait time %s does not fit in 3 characters' % value)
        
        return result