"""
@package mi.instrument.pco2a.pco2pro.ooicore.driver
@file marine-integrations/mi/instrument/pco2a/pco2pro/ooicore/driver.py
@author E. Hahn
@brief Driver for the ooicore
Release notes:

Initial revision.
"""

__author__ = 'E. Hahn'
__license__ = 'Apache 2.0'

import re
import string

from mi.core.log import get_logger ; log = get_logger()

from mi.core.exceptions import SampleException
from mi.core.exceptions import InstrumentProtocolException
from mi.core.exceptions import InstrumentParameterException

from mi.core.common import BaseEnum
from mi.core.instrument.instrument_protocol import CommandResponseInstrumentProtocol
from mi.core.instrument.instrument_fsm import InstrumentFSM
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
    AIR_SAMPLE = 'air_sample'
    WATER_SAMPLE = 'water_sample'

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
    ACQUIRE_STATUS = DriverEvent.ACQUIRE_STATUS
    DISCOVER_COMMAND = 'PROTOCOL_EVENT_DISCOVER_COMMAND'
    WAIT_FOR_COMMAND = 'PROTOCOL_EVENT_WAIT_FOR_COMMAND'
    START_COMMAND = 'PROTOCOL_EVENT_START_COMMAND'
    
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
    ACQUIRE_STATUS = ProtocolEvent.ACQUIRE_STATUS
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
    TOGGLE_ATM_MODE = 'TOGGLE_ATM_MODE'
    SET_MENU_WAIT_TIME = 'SET_MENU_WAIT_TIME'
    CONFIRM_SET_TIME = 'CONFIRM_SET_TIME'
    
# lines up with Command    
COMMAND_CHAR = {
    'BACK_MENU': '0',
    'SPACE': 0x20,
    'SET_CLOCK': '4',
    'CHANGE_PARAM': '5',
    'CHANGE_AUTO_START_MODE': '1',
    'START_AUTOSAMPLE': '7',
    'CHANGE_NUMBER_SAMPLES': '2',
    'TOGGLE_ATM_MODE': '4',
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
    TOGGLE_ATM_MODE = 'SUBMENU_TOGGLE_ATM_MODE'
    SET_MENU_WAIT_TIME = 'SUBMENU_SET_MENU_WAIT_TIME'
     
class Prompt(BaseEnum):
    """
    Device i/o prompts and menus
    """
    MAIN_MENU = "1) Record Data Now           5) Auto Start Settings\n" 
    "2) View Logged Data          6) Atmosphere Menu\n" 
    "3) Erase Logged Data         7) Sleep Now\n" 
    "4) Change Clock Time         8) Display SBA Console\n\nEnter Command >" 
    AUTO_START_MENU="1) Change Auto Start Program\n" 
    "2) Change Number of Samples\n3) Change Re-Zero Interval\n" 
    "4) Toggle ATM Mode\n5) Reset Zero Count\n6) Change Menu Timer\n" 
    "0) Return to Main Menu\n\nEnter Command >"
    AUTO_START_PROMPT= "Please select one of the following modes to autostart the logger with\n\n0. No Auto Start\n1. " 
    "30 Minute Auto Start\n2. 1 Hour Auto start\n3. 3 Hour Auto Start\n4. 6 Hour Auto Start\n5. 12 Hour Auto Start\n6. " 
    "Daily Auto Start\n7. Continuous (Always Logging)\n\n>"
    CHANGE_NUMBER_SAMPLES="Please enter the amount of samples to take each time " 
    "unit is automatically started [1-9]\n\n>" 
    MENU_WAIT_TIME="Please enter the number of seconds to wait for Menu to " 
    "timeout without input [###]\n\n>"
    SET_TIME_CONFIRM="Would you still like to change the time?(Y/N)"
    SET_YEAR="Enter Year [####]"
    SET_MONTH="Enter Month [##]"
    SET_DAY="Enter Day [##]"
    SET_HOUR="Enter Hour [##]"
    SET_MIN="Enter Minute [##]"
    # Seconds are not missing, they are not entered
    
MENU_PROMPTS = [Prompt.MAIN_MENU, Prompt.AUTO_START_MENU,
                Prompt.AUTO_START_PROMPT, Prompt.SET_TIME_CONFIRM,
                Prompt.CHANGE_NUMBER_SAMPLES, Prompt.MENU_WAIT_TIME]

MENU = MenuInstrumentProtocol.MenuTree({
    SubMenu.MAIN:[],
    SubMenu.SET_CLOCK:[Directions(command=Command.SET_CLOCK,
                                   response=Prompt.SET_TIME_CONFIRM)],
    SubMenu.CHANGE_PARAM:[Directions(command=Command.CHANGE_PARAM,
                          response=Prompt.AUTO_START_MENU)],
    SubMenu.CHANGE_AUTO_START_MODE:[Directions(SubMenu.CHANGE_PARAM),
                                    Directions(command=Command.CHANGE_AUTO_START_MODE,
                                     response=Prompt.AUTO_START_PROMPT)],
    SubMenu.CHANGE_NUMBER_SAMPLES:[Directions(SubMenu.CHANGE_PARAM),
                                   Directions(command=Command.CHANGE_NUMBER_SAMPLES,
                                     response=Prompt.CHANGE_NUMBER_SAMPLES)],
    SubMenu.SET_MENU_WAIT_TIME:[Directions(SubMenu.CHANGE_PARAM),
                                Directions(command=Command.SET_MENU_WAIT_TIME,
                                response=Prompt.MENU_WAIT_TIME)]
})
      
   
    
###############################################################################
# Matchers - Data particles and prompts
###############################################################################
AIR_REGEX = r'#(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}), (M),(\d{5}),(\d{5}),(\d{3}.\d),(\d{2}.\d),(\d{2}.\d{3}),(\d{2}.\d{3}),(\d{4}),(\d{2}.\d),(\d{2}.\d),A'
AIR_REGEX_MATCHER = re.compile(AIR_REGEX)

WATER_REGEX = r'#(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}), (M),(\d{5}),(\d{5}),(\d{3}.\d),(\d{2}.\d),(\d{2}.\d{3}),(\d{2}.\d{3}),(\d{4}),(\d{2}.\d),(\d{2}.\d),W'
WATER_REGEX_MATCHER = re.compile(WATER_REGEX)

ESCAPE_AUTO_START_REGEX = r"Press Space-bar to escape Auto-Start ( \d+ Seconds )...."
ESCAPE_AUTO_START_MATCHER = re.compile(ESCAPE_AUTO_START_REGEX)


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
    _data_particle_type = DataParticleType.AIR_SAMPLE

    def _build_parsed_values(self):
        """
        Parse air sample values from raw data into a dictionary
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
    _data_particle_type = DataParticleType.WATER_SAMPLE
    
    def _build_parsed_values(self):
        """
        Parse air sample values from raw data into a dictionary
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
        self._protocol_fsm = InstrumentFSM(ProtocolState, ProtocolEvent,
                            ProtocolEvent.ENTER, ProtocolEvent.EXIT)

        # Add event handlers for protocol state machine.
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.ENTER, self._handler_unknown_enter)
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.DISCOVER_COMMAND, self._handler_discover_command)
                
        self._protocol_fsm.add_handler(ProtocolState.DISCOVERY, ProtocolEvent.DISCOVER_COMMAND, self._handler_discover_command)
        self._protocol_fsm.add_handler(ProtocolState.DISCOVERY, ProtocolEvent.START_DIRECT, self._handler_command_start_direct)
               
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.ENTER, self._handler_command_enter)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_DIRECT, self._handler_command_start_direct)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.GET, self._handler_command_get)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.SET, self._handler_command_set)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.ACQUIRE_STATUS, self._handler_command_acquire_status)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.CLOCK_SYNC, self._handler_command_clock_sync)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_AUTOSAMPLE, self._handler_command_autosample)
                                       
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.ENTER, self._handler_autosample_enter)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.STOP_AUTOSAMPLE, self._handler_autosample_exit)
        
        self._protocol_fsm.add_handler(ProtocolState.WAIT_FOR_COMMAND, ProtocolEvent.ENTER, self._handler_wait_for_command_enter)
        
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.ENTER, self._handler_direct_access_enter)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.EXIT, self._handler_direct_access_exit)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.STOP_DIRECT, self._handler_direct_access_stop_direct)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.EXECUTE_DIRECT, self._handler_direct_access_execute_direct)
        
        # Construct the parameter dictionary containing device parameters,
        # current parameter values, and set formatting functions.
        self._build_driver_dict()
        self._build_param_dict()
        self._build_command_dict()

        # Add build handlers for device commands.
        self._add_build_handler(Command.BACK_MENU, self._build_menu_command)
        self._add_build_handler(Command.START_AUTOSAMPLE, self._build_menu_command)
        self._add_build_handler(Command.CHANGE_AUTO_START_MODE, self._build_menu_command)
        self._add_build_handler(Command.CHANGE_NUMBER_SAMPLES, self._build_menu_command)
        self._add_build_handler(Command.SET_CLOCK, self._build_menu_command)
        self._add_build_handler(Command.SET_MENU_WAIT_TIME, self._build_menu_command)
        self._add_build_handler(Command.TOGGLE_ATM_MODE, self._build_menu_command)
        
        # Add response handlers for device commands.
        self._add_response_handler(Command.BACK_MENU, self._parse_menu_change_response)
        self._add_response_handler(Command.CHANGE_PARAM, self._parse_show_param_response)
        self._add_response_handler(Command.CHANGE_AUTO_START_MODE, self._parse_menu_change_response)
        self._add_response_handler(Command.CHANGE_NUMBER_SAMPLES, self._parse_menu_change_response)
        self._add_response_handler(Command.SET_CLOCK, self._parse_menu_change_response)
        self._add_response_handler(Command.SET_MENU_WAIT_TIME, self._parse_menu_change_response)
        

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
        """

        return_list = []

        sieve_matchers = [ AIR_REGEX_MATCHER,
                           WATER_REGEX_MATCHER,
                           ESCAPE_AUTO_START_MATCHER]

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
                             r'Number of Samples: (\d+)',
                             lambda match : match.group(1),
                             self._int_to_string,
                             type=ParameterDictType.INT,
                             startup_param=True,
                             direct_access=False,
                             default_value=5,
                             menu_path_read=SubMenu.CHANGE_PARAM,
                             submenu_read=[],
                             menu_path_write=SubMenu.CHANGE_PARAM,
                             submenu_write=[["2", Prompt.CHANGE_NUMBER_SAMPLES]],
                             display_name="number samples"
        )
        self._param_dict.add(Parameter.MENU_WAIT_TIME,
                             r'Menu Timeout: *(\d+)',
                             lambda match : match.group(1),
                             self._int_to_string,
                             type=ParameterDictType.INT,
                             startup_param=True,
                             direct_access=False,
                             default_value=20,
                             menu_path_read=SubMenu.CHANGE_PARAM,
                             submenu_read=[],
                             menu_path_write=SubMenu.CHANGE_PARAM,
                             submenu_write=[["6", Prompt.MENU_WAIT_TIME]],
                             display_name="menu wait time"
        )
        self._param_dict.add(Parameter.AUTO_SAMPLE_MODE,
                             r'Auto Start Program: ([a-zA-Z0-9]+|[a-zA-Z0-9]+ [a-zA-Z0-9]+)',
                             lambda match : self._to_autosample(match.group(1)),
                             str,
                             type=ParameterDictType.STRING,
                             startup_param=True,
                             direct_access=False,
                             default_value=AutoSampleMode.HALF_HR_SAMPLE,
                             menu_path_read=SubMenu.CHANGE_PARAM,
                             submenu_read=[],
                             menu_path_write=SubMenu.CHANGE_AUTO_START_MODE,
                             submenu_write=[["1", Prompt.AUTO_START_PROMPT]],
                             display_name="auto sample mode"                             
        )
                             
        
    def _build_command_dict(self):
        """
        Populate the command dictionary with command.
        """
        self._cmd_dict.add(Capability.ACQUIRE_STATUS, display_name="acquire status")
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
        
        # try to match the chunk 
        auto_start_match = ESCAPE_AUTO_START_MATCHER.match(chunk)
        
        current_state = self._protocol_fsm.get_current_state()
        if auto_start_match:
            if current_state_in [ProtocolState.UNKNOWN,
                                 ProtocolState.DISCOVERY]:
                # send space to tell instrument to not start auto sampling
                self._connection.send("%c" % COMMAND_CHAR[Command.SPACE])
                # do initialization
                
                # throw event to put into command mode
                self._protocol_fsm.on_event(ProtocolEvent.DISCOVER_COMMAND)
            elif current_state is ProtocolState.WAIT_FOR_COMMAND:
                # send space to tell instrument to not start auto sampling
                self._connection.send("%c" % COMMAND_CHAR[Command.SPACE])
                # throw event to put into command mode
                self._protocol_fsm.on_event(ProtocolEvent.DISCOVER_COMMAND)
                   
    def _filter_capabilities(self, events):
        """
        Return a list of currently available capabilities.
        """
        return [x for x in events if Capability.has(x)]
    
    ########################################################################
    # Command builders
    ########################################################################
    def _build_menu_command(self, cmd):
        """
        Pick the right command character 
        @raises InstrumentProtocolException if a command with an unknown
        corresponding character is input
        """
        if COMMAND_CHAR[cmd]:
            return COMMAND_CHAR[cmd]
        else:
            raise InstrumentProtocolException("Unknown command character for %s" % cmd)
        
    ########################################################################
    # Command parsers
    ########################################################################
    def _parse_menu_change_response(self, response, prompt):
        """ Parse a response to a menu change
        
        @param response What was sent back from the command that was sent
        @param prompt The prompt that was returned from the device
        @retval The prompt that was encountered after the change
        """
        log.trace("Parsing menu change response with prompt: %s", prompt)
        return prompt
    
    def _parse_show_param_response(self, response, prompt):
        """ Parse the show parameter response screen """
        log.trace("Parsing show parameter screen")
        self._param_dict.update_many(response)
    
    ########################################################################
    # Utilities
    ########################################################################
    def _go_to_root_menu(self):
        """ Get back to the root menu, assuming we are in COMMAND mode.
        Getting to command mode should be done before this method is called.
        """
        log.debug("Returning to root menu...")
        
        # BACK MENU will re-display the main menu, or bring you back from
        # the auto start menu
        try:
            response = self._do_cmd_resp(Command.BACK_MENU)
        
            while not str(response).lstrip().endswith(Prompt.MAIN_MENU):
                response = self._do_cmd_resp(Command.BACK_MENU)
                time.sleep(1)
        except InstrumentTimeoutException:
            raise InstrumentProtocolException("Not able to get valid command prompt. "
                                              "Is instrument in command mode?")
        
    def _send_data_chars(self, data_chars, expected_prompt_in=None):
        """
        Send a set of data characters
        @param data_chars A data character string
        @param expected_response_in The expected response
        """
        
        # loop and send each of the characters in the string
        self._connection.send(data_chars)
            
        # get the response
        timeout = 5
        (response, result) = self._get_raw_response(timeout, expected_prompt=expected_prompt_in)
        
        return (response, result)
        
    def _update_params(self):
        """Fetch the parameters from the device, and update the param dict.
        
        @param args Unused
        @param kwargs Takes timeout value
        @throw InstrumentProtocolException
        @throw InstrumentTimeoutException
        """
        log.debug("Updating parameter dict")
        old_config = self._param_dict.get_config()
        self._get_config()
        new_config = self._param_dict.get_config()            
        if (new_config != old_config):
            self._driver_event(DriverAsyncEvent.CONFIG_CHANGE)  
    
    def _get_config(self, *args, **kwargs):
        """ Get the entire configuration for the instrument
        
        @param params The parameters and values to set
        Should be a dict of parameters and values
        @throw InstrumentProtocolException On a deeper issue
        """
        # Just need to show the parameter screen...the parser for the command
        # does the update_many()
        self._go_to_root_menu()
        self._navigate(SubMenu.CHANGE_PARAM)
        self._go_to_root_menu()
        
    def _initialize_params(self):
        """
        Initialize startup parameters
        """
        for param in self._param_dict.get:
            if not Parameter.has(param):
                raise InstrumentParameterException()

            self._go_to_root_menu()
            # Only try to change them if they arent set right as it is
            log.trace("Setting parameter: %s, current paramdict value: %s, init val: %s",
                      param, self._param_dict.get(param),
                      self._param_dict.get_init_value(param))
            if (self._param_dict.get(param) != self._param_dict.get_init_value(param)):
                # all parameters are changed from the same menu
                self._navigate(SubMenu.CHANGE_PARAM)
                if (param == Parameter.AUTO_SAMPLE_MODE):
                    result = self._do_cmd_resp(Command.CHANGE_AUTO_START_MODE,
                                               self._param_dict.get_init_value(param),
                                               expected_prompt=Prompt.AUTO_START_PROMPT)
                    if not result:
                        raise InstrumentParameterException("Could not set param %s" % param)
                elif (param == Parameter.NUMBER_SAMPLES):
                    result = self._do_cmd_resp(Command.CHANGE_NUMBER_SAMPLES,
                                               self._param_dict.get_init_value(param),
                                               expected_prompt=Prompt.CHANGE_NUMBER_SAMPLES)
                    if not result:
                        raise InstrumentParameterException("Could not set param %s" % param)
                elif (param == Parameter.MENU_WAIT_TIME):
                    result = self._do_cmd_resp(Command.SET_MENU_WAIT_TIME,
                                               self._param_dict.get_init_value(param),
                                               expected_prompt=Prompt.MENU_WAIT_TIME)
                    if not result:
                        raise InstrumentParameterException("Could not set param %s" % param)
                    
        self._update_params()

    ########################################################################
    # Unknown handlers.
    ########################################################################

    def _handler_unknown_enter(self, *args, **kwargs):
        """
        Enter unknown state, then enter discovery.
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        
        # Go directly into discovery
        next_state = ProtocolState.DISCOVERY
        next_agent_state = ResourceAgentState.UNINITIALIZED
        return (next_state, next_agent_state)
        
    
    ########################################################################
    # Discovery handlers
    ########################################################################
    def _handler_discover_enter(self, *args, **kwargs):
        """
        Find out if we are already in command mode
        """
        next_state = None
        next_agent_state = None
        
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        
        # if the main menu prints, we are already in command mode,
        # otherwise need to wait for opportunity to escape auto sampling
        timeout = 5
        self._connection.send("%c" % COMMAND_CHAR[Command.SPACE])
        (prompt, result) = self._get_raw_response(timeout, 
                    expected_prompt=[Prompt.MAIN_MENU])
        if prompt == Prompt.MAIN_MENU:
            # do initialization
            self._initialize_params()
            
            # go into command state
            next_state = ProtocolState.COMMAND
            next_agent_state = ResourceAgentState.COMMAND
            
        return (next_state, next_agent_state)
    
    def _handler_discover_command(self):
        """
        Do initialization then Enter command mode
        """
        # do initialization
        self._initialize_params()
        
        # set to command mode
        next_state = ProtocolState.COMMAND
        next_agent_state = ResourceAgentState.COMMAND
        return (next_state, next_agent_state)

       
    ########################################################################
    # Command handlers.
    ########################################################################

    def _handler_command_enter(self, *args, **kwargs):
        """
        Enter command state.
        @throws InstrumentTimeoutException if the device cannot be woken.
        @throws InstrumentProtocolException if the update commands and not recognized.
        """
        # Command device to update parameters and send a config change event.
        #self._update_params()

        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

    def _handler_command_get(self, *args, **kwargs):
        """
        Get parameters while in the command state.
        @param params List of the parameters to pass to the state
        @retval returns (next_state, result) where result is a dict {}. No
            agent state changes happening with Get, so no next_agent_state
        @throw InstrumentParameterException for invalid parameter
        """
        next_state = None
        result = None
        result_vals = {}
        
        if (params == None):
            raise InstrumentParameterException("GET parameter list empty!")
            
        if (params == Parameter.ALL):
            params = [Parameter.AUTO_SAMPLE_MODE, Parameter.MENU_WAIT_TIME,
                      Parameter.NUMBER_SAMPLES]
            
        if not isinstance(params, list):
            raise InstrumentParameterException("GET parameter list not a list!")

        # Do a bulk update from the instrument since they are all on one page
        self._update_params()
        
        # fill the return values from the update
        for param in params:
            if not Parameter.has(param):
                raise InstrumentParameterException("Invalid parameter!")
            result_vals[param] = self._param_dict.get(param) 
        result = result_vals

        log.debug("Get finished, next: %s, result: %s", next_state, result) 
        return (next_state, result)
    
    def _handler_command_acquire_status(self, *args, **kwargs):
        """
        Acquire status by updating all the parameters
        """
        next_state = None
        result = None
        result_vals = {}
        
        # Do a bulk update from the instrument since they are all on one page
        self._update_params()
        
        params = [Parameter.AUTO_SAMPLE_MODE, Parameter.MENU_WAIT_TIME,
                      Parameter.NUMBER_SAMPLES]
        
        # fill the return values from the update
        for param in params:
            if not Parameter.has(param):
                raise InstrumentParameterException("Invalid parameter!")
            result_vals[param] = self._param_dict.get(param) 
        result = result_vals

        log.debug("Acquire status finished, next: %s, result: %s", next_state, result) 
        return (next_state, result)

    def _handler_command_set(self, params, *args, **kwargs):
        """
        Handle setting data from command mode
         
        @param params Dict of the parameters and values to pass to the state
        @retval return (next state, result)
        @throw InstrumentProtocolException For invalid parameter
        """
        next_state = None
        result = None
        result_vals = {}    

        if ((params == None) or (not isinstance(params, dict))):
            raise InstrumentParameterException()
        
        name_values = params
        for key in name_values.keys():
            if not Parameter.has(key):
                raise InstrumentParameterException()
            
            value = name_values[key]
            # all parameters are read/write and are in the auto start menu
            self._navigate(SubMenu.CHANGE_PARAM)
            if (key == Parameter.AUTO_SAMPLE_MODE):
                # convert from auto sample enum to character to input which
                # selects a specific auto sampling mode
                autosample_mode_char = self._from_autosample(value)
                
                try:                
                    self._do_cmd_resp(Command.CHANGE_AUTO_START_MODE, autosample_mode_char,
                                      expected_prompt=[Prompt.AUTO_START_MENU])
                except InstrumentProtocolException:
                    self._go_to_root_menu()
                    raise InstrumentProtocolException("Could not set auto sampling mode")
                
                # Populate with actual value set
                result_vals[key] = name_values[key]
            elif (key == Parameter.MENU_WAIT_TIME):
                try:                
                    self._do_cmd_resp(Command.SET_MENU_WAIT_TIME, value,
                                      expected_prompt=[Prompt.AUTO_START_MENU])
                except InstrumentProtocolException:
                    self._go_to_root_menu()
                    raise InstrumentProtocolException("Could not set menu wait time")
                
                # Populate with actual value set
                result_vals[key] = name_values[key]
            elif (key == Parameter.NUMBER_SAMPLES):
                try:                
                    self._do_cmd_resp(Command.CHANGE_NUMBER_SAMPLES, value,
                                      expected_prompt=[Prompt.AUTO_START_MENU])
                except InstrumentProtocolException:
                    self._go_to_root_menu()
                    raise InstrumentProtocolException("Could not set menu wait time")
                
                # Populate with actual value set
                result_vals[key] = name_values[key]

        self._go_to_root_menu()
        self._update_params()
        
        result = result_vals
            
        log.debug("next: %s, result: %s", next_state, result) 

        return (next_state, result)
    
    def _handler_command_clock_sync(self, date_time_str, *args, **kwargs):
        """
        Handle setting the clock.  This requires stepping through many menu steps
        @param param A date time string with format 'YYYY/MM/DD HH:MM:SS'
        @throw InstrumentProtocolException For invalid parameter
        """
        next_state = None
        result = None
        
        if ((date_time_str == None) or (not isinstance(date_time_str, string))):
            # if none is entered, for now just set string here
            date_time_str = '2013/05/24 12:30:00'
            #raise InstrumentParameterException()
        
        self._navigate(SubMenu.MAIN)
        response = self._do_cmd_resp(Command.SET_CLOCK,
                                     expected_prompt=[Prompt.SET_TIME_CONFIRM])
        if response == Prompt.SET_TIME_CONFIRM:
            response = self._do_cmd_resp(Command.CONFIRM_SET_TIME,
                                         expected_prompt=[Prompt.SET_YEAR])
            if response == Prompt.SET_YEAR:
                response = self._send_data_chars(data_chars=date_time_str[0:4],
                                                 expected_prompt_in=[Prompt.SET_MONTH])
                if response == Prompt.SET_MONTH:
                    response = self._send_data_chars(data_chars=date_time_str[5:7],
                                                     expected_prompt_in=[Prompt.SET_DAY])
                    if response == Prompt.SET_DAY:
                        response = self._send_data_chars(data_chars=date_time_str[8:10],
                                                         expected_prompt_in=[Prompt.SET_HOUR])
                        if response == Prompt.SET_HOUR:
                            response = self._send_data_chars(data_chars=date_time_str[11:13],
                                                             expected_prompt_in=[Prompt.SET_MIN])
                            if response == Prompt.SET_MIN:
                                (response, result) = self._send_data_chars(data_chars=date_time_str[14:16],
                                                                           expected_prompt_in=[Prompt.MAIN_MENU])
                                
        return (next_state, result)
    
    def _handler_command_autosample(self, *args, **kwargs):
        """
        Start autosample mode
        """
        next_state = None
        next_agent_state = None
        result = None
        
        self._navigate(SubMenu.MAIN)
        self._do_cmd_no_resp(Command.START_AUTOSAMPLE)
        
        next_state = ProtocolState.AUTOSAMPLE
        next_agent_state = ResourceAgentState.STREAMING
        
        return (next_state, (next_agent_state, result))
      

    def _handler_command_start_direct(self):
        """
        Start direct access
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

    def _handler_autosample_exit(self):
        """
        Stop autosample mode - In modes other than continuous sampling, this
        requires waiting for the escape from auto sampling to happen.  In
        continuous mode, you can send a space to escape.
        """
        next_state = None
        next_agent_state = None
        result = None
        
        timeout=5
        self._connection.send("%c" % COMMAND_CHAR[Command.SPACE])
        (prompt, result) = self._get_raw_response(timeout,
                                       expected_prompt=[Prompt.MAIN_MENU])
        if prompt == Prompt.MAIN_MENU:
            next_state = ProtocolState.COMMAND
            next_agent_state = ResourceAgentState.COMMAND
        else:
            next_state = ProtocolState.WAIT_FOR_COMMAND
            next_agent_state = ResourceAgentState.STREAMING
        
        return (next_state, (next_agent_state, result))
    
    ########################################################################
    # Wait for command handlers
    ########################################################################
    def _handler_wait_for_command_enter(self):
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
    
        
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
        @throw InstrumentProtocolException on invalid command
        """
        next_state = None
        result = None

        next_state = ProtocolState.COMMAND
        next_agent_state = ResourceAgentState.COMMAND

        return (next_state, (next_agent_state, result))
    
    @staticmethod
    def _to_autosample(value):
        """
        Convert auto sampling string to auto sampling enum
        """
        if not value in AUTO_SAMPLE_STR:
            raise InstrumentProtocolException(
                'Value %s is not a member of the AutoSampleMode enum.' % value)
         
        return AUTO_SAMPLE_STR[value]
            

    @staticmethod
    def _from_autosample(value):
        """
        Converts from auto sampling mode enum to character to select auto sampling mode
        """
        if not value in AUTO_SAMPLE_MENU_OPTS:
            raise InstrumentParameterException(
                'Value %s is not an auto sample mode enum' % value)
            
        return AUTO_SAMPLE_MENU_OPTS[value]       
