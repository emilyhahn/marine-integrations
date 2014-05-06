"""
@package mi.instrument.nortek.driver
@file mi/instrument/nortek/driver.py
@author Rachel Manoni
@author Ronald Ronquillo
@brief Base class for Nortek instruments
"""
import struct
from mi.core.driver_scheduler import DriverSchedulerConfigKey, TriggerType

__author__ = 'Ronald Ronquillo'
__license__ = 'Apache 2.0'

import re
import time
import copy
import base64

from mi.core.log import get_logger ; log = get_logger()

from mi.core.instrument.instrument_fsm import InstrumentFSM

from mi.core.instrument.data_particle import DataParticle, DataParticleKey, DataParticleValue
from mi.core.instrument.data_particle import CommonDataParticleType
from mi.core.instrument.instrument_protocol import CommandResponseInstrumentProtocol
from mi.core.instrument.driver_dict import DriverDict, DriverDictKey
from mi.core.instrument.protocol_cmd_dict import ProtocolCommandDict
from mi.core.instrument.protocol_param_dict import ParameterDictVisibility
from mi.core.instrument.protocol_param_dict import ProtocolParameterDict
from mi.core.instrument.protocol_param_dict import RegexParameter
from mi.core.instrument.protocol_param_dict import ParameterDictType

from mi.core.instrument.instrument_driver import DriverEvent
from mi.core.instrument.instrument_driver import DriverConfigKey
from mi.core.instrument.instrument_driver import SingleConnectionInstrumentDriver
from mi.core.instrument.instrument_driver import DriverAsyncEvent
from mi.core.instrument.instrument_driver import DriverProtocolState
from mi.core.instrument.instrument_driver import DriverParameter
from mi.core.instrument.instrument_driver import ResourceAgentState

from mi.core.exceptions import ReadOnlyException
from mi.core.exceptions import InstrumentStateException
from mi.core.exceptions import InstrumentTimeoutException
from mi.core.exceptions import InstrumentProtocolException
from mi.core.exceptions import InstrumentParameterException
from mi.core.exceptions import SampleException

from mi.core.time import get_timestamp_delayed
from mi.core.common import BaseEnum

# newline.
NEWLINE = '\n\r'

# default timeout.
TIMEOUT = 10
# set up the 'structure' lengths (in bytes) and sync/id/size constants
USER_CONFIG_LEN = 512
USER_CONFIG_SYNC_BYTES = '\xa5\x00\x00\x01'
HW_CONFIG_LEN = 48
HW_CONFIG_SYNC_BYTES   = '\xa5\x05\x18\x00'
HEAD_CONFIG_LEN = 224
HEAD_CONFIG_SYNC_BYTES = '\xa5\x04\x70\x00'

BV_LEN = 4
CLK_LEN = 8
ID_LEN = 16
INTVL_LEN = 4

CHECK_SUM_SEED = 0xb58c

HARDWARE_CONFIG_DATA_PATTERN = r'%s(.{14})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{12})(.{4})(.{2})' % HW_CONFIG_SYNC_BYTES
HARDWARE_CONFIG_DATA_REGEX = re.compile(HARDWARE_CONFIG_DATA_PATTERN, re.DOTALL)
HEAD_CONFIG_DATA_PATTERN = r'%s(.{2})(.{2})(.{2})(.{12})(.{176})(.{22})(.{2})(.{2})' % HEAD_CONFIG_SYNC_BYTES
HEAD_CONFIG_DATA_REGEX = re.compile(HEAD_CONFIG_DATA_PATTERN, re.DOTALL)
USER_CONFIG_DATA_PATTERN = r'%s(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})' \
                           r'(.{2})(.{2})(.{2})(.{2})(.{2})(.{6})(.{2})(.{6})(.{4})(.{2})(.{2})(.{2})(.{2})(.{2})' \
                           r'(.{2})(.{2})(.{2})(.{2})(.{180})(.{180})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})' \
                           r'(.{2})(.{2})(.{2})(.{2})(.{2})(.{2})(.{30})(.{16})(.{2})' % USER_CONFIG_SYNC_BYTES
USER_CONFIG_DATA_REGEX = re.compile(USER_CONFIG_DATA_PATTERN, re.DOTALL)

CLOCK_DATA_PATTERN = r'(.{1})(.{1})(.{1})(.{1})(.{1})(.{1})'
#r'r'(.{1})(.{1})(.{1})(.{1})(.{1})(.{1})\x06\x06'
CLOCK_DATA_REGEX = re.compile(CLOCK_DATA_PATTERN, re.DOTALL)
BATTERY_DATA_PATTERN = r'(.{2})\x06\x06'
BATTERY_DATA_REGEX = re.compile(BATTERY_DATA_PATTERN, re.DOTALL)
ID_DATA_PATTERN = r'(.{8})'
#ID_DATA_PATTERN = r'(.{14})\x06\x06'
ID_DATA_REGEX = re.compile(ID_DATA_PATTERN, re.DOTALL)

NORTEK_COMMON_SAMPLE_STRUCTS = [[USER_CONFIG_SYNC_BYTES, USER_CONFIG_LEN],
                                [HW_CONFIG_SYNC_BYTES, HW_CONFIG_LEN],
                                [HEAD_CONFIG_SYNC_BYTES, HEAD_CONFIG_LEN]]

NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS = []

INTERVAL_TIME_REGEX = r"([0-9][0-9]:[0-9][0-9]:[0-9][0-9])"

class ScheduledJob(BaseEnum):
    CLOCK_SYNC = 'clock_sync'
    ACQUIRE_STATUS = 'acquire_status'


class NortekDataParticleType(BaseEnum):
    RAW = CommonDataParticleType.RAW
    HARDWARE_CONFIG = 'vel3d_cd_hardware_configuration'
    HEAD_CONFIG = 'vel3d_cd_head_configuration'
    USER_CONFIG = 'vel3d_cd_user_configuration'
    CLOCK = 'vel3d_clock_data'
    BATTERY = 'vel3d_cd_battery_voltage'
    ID_STRING = 'vel3d_cd_identification_string'


class InstrumentPrompts(BaseEnum):
    """
    Device prompts.
    """
    COMMAND_MODE  = 'Command mode'
    CONFIRMATION  = 'Confirm:'
    Z_ACK         = '\x06\x06'  # attach a 'Z' to the front of these two items to force them to the end of the list
    Z_NACK        = '\x15\x15'  # so the other responses will have priority to be detected if they are present


class InstrumentCmds(BaseEnum):
    CONFIGURE_INSTRUMENT               = 'CC'        # sets the user configuration
    SOFT_BREAK_FIRST_HALF              = '@@@@@@'
    SOFT_BREAK_SECOND_HALF             = 'K1W%!Q'
    READ_REAL_TIME_CLOCK               = 'RC'
    SET_REAL_TIME_CLOCK                = 'SC'
    CMD_WHAT_MODE                      = 'II'        # to determine the mode of the instrument
    READ_USER_CONFIGURATION            = 'GC'
    READ_HW_CONFIGURATION              = 'GP'
    READ_HEAD_CONFIGURATION            = 'GH'
    POWER_DOWN                         = 'PD'
    READ_BATTERY_VOLTAGE               = 'BV'
    READ_ID                            = 'ID'
    START_MEASUREMENT_WITHOUT_RECORDER = 'ST'
    ACQUIRE_DATA                       = 'AD'
    CONFIRMATION                       = 'MC'        # confirm a break request
    #SAMPLE_AVG_TIME                    = 'A'
    #SAMPLE_INTERVAL_TIME               = 'M'
    #GET_ALL_CONFIGURATIONS             = 'GA'
    #SAMPLE_WHAT_MODE                   = 'I'


class InstrumentModes(BaseEnum):
    """
    List of possible modes the instrument can be in
    """
    FIRMWARE_UPGRADE = '\x00\x00\x06\x06'
    MEASUREMENT      = '\x01\x00\x06\x06'
    COMMAND          = '\x02\x00\x06\x06'
    DATA_RETRIEVAL   = '\x04\x00\x06\x06'
    CONFIRMATION     = '\x05\x00\x06\x06'


class ProtocolState(BaseEnum):
    """
    Protocol states enum.
    """
    UNKNOWN = DriverProtocolState.UNKNOWN
    COMMAND = DriverProtocolState.COMMAND
    AUTOSAMPLE = DriverProtocolState.AUTOSAMPLE
    DIRECT_ACCESS = DriverProtocolState.DIRECT_ACCESS


class ProtocolEvent(BaseEnum):
    """
    Protocol events
    """
    # common events from base class
    ENTER = DriverEvent.ENTER
    EXIT = DriverEvent.EXIT
    GET = DriverEvent.GET
    SET = DriverEvent.SET
    DISCOVER = DriverEvent.DISCOVER
    ACQUIRE_SAMPLE = DriverEvent.ACQUIRE_SAMPLE
    ACQUIRE_STATUS = DriverEvent.ACQUIRE_STATUS
    START_AUTOSAMPLE = DriverEvent.START_AUTOSAMPLE
    STOP_AUTOSAMPLE = DriverEvent.STOP_AUTOSAMPLE
    START_DIRECT = DriverEvent.START_DIRECT
    STOP_DIRECT = DriverEvent.STOP_DIRECT
    EXECUTE_DIRECT = DriverEvent.EXECUTE_DIRECT
    CLOCK_SYNC = DriverEvent.CLOCK_SYNC
    SCHEDULED_CLOCK_SYNC = DriverEvent.SCHEDULED_CLOCK_SYNC
    RESET = DriverEvent.RESET

    # instrument specific events
    SET_CONFIGURATION = "PROTOCOL_EVENT_CMD_SET_CONFIGURATION"
    READ_CLOCK = "PROTOCOL_EVENT_CMD_READ_CLOCK"
    READ_MODE = "PROTOCOL_EVENT_CMD_READ_MODE"
    POWER_DOWN = "PROTOCOL_EVENT_CMD_POWER_DOWN"
    READ_BATTERY_VOLTAGE = "PROTOCOL_EVENT_CMD_READ_BATTERY_VOLTAGE"
    READ_ID = "PROTOCOL_EVENT_CMD_READ_ID"
    GET_HW_CONFIGURATION = "PROTOCOL_EVENT_CMD_GET_HW_CONFIGURATION"
    GET_HEAD_CONFIGURATION = "PROTOCOL_EVENT_CMD_GET_HEAD_CONFIGURATION"
    GET_USER_CONFIGURATION = "PROTOCOL_EVENT_GET_USER_CONFIGURATION"
    SCHEDULED_ACQUIRE_STATUS = "PROTOCOL_EVENT_SCHEDULED_ACQUIRE_STATUS"


class Capability(BaseEnum):
    """
    Capabilities that are exposed to the user (subset of above)
    """
    GET = ProtocolEvent.GET
    SET = ProtocolEvent.SET
    ACQUIRE_SAMPLE = ProtocolEvent.ACQUIRE_SAMPLE
    START_AUTOSAMPLE = ProtocolEvent.START_AUTOSAMPLE
    STOP_AUTOSAMPLE = ProtocolEvent.STOP_AUTOSAMPLE
    CLOCK_SYNC = ProtocolEvent.CLOCK_SYNC
    # SET_CONFIGURATION = ProtocolEvent.SET_CONFIGURATION
    # READ_CLOCK = ProtocolEvent.READ_CLOCK
    # READ_MODE = ProtocolEvent.READ_MODE
    # POWER_DOWN = ProtocolEvent.POWER_DOWN
    # READ_BATTERY_VOLTAGE = ProtocolEvent.READ_BATTERY_VOLTAGE
    # READ_ID = ProtocolEvent.READ_ID
    # GET_HW_CONFIGURATION = ProtocolEvent.GET_HW_CONFIGURATION
    # GET_HEAD_CONFIGURATION = ProtocolEvent.GET_HEAD_CONFIGURATION
    # GET_USER_CONFIGURATION = ProtocolEvent.GET_USER_CONFIGURATION
    START_DIRECT = DriverEvent.START_DIRECT
    STOP_DIRECT = DriverEvent.STOP_DIRECT
    ACQUIRE_STATUS = DriverEvent.ACQUIRE_STATUS


# Device specific parameters.
class Parameter(DriverParameter):
    """
    Device parameters
    """
    # user configuration
    TRANSMIT_PULSE_LENGTH = "TransmitPulseLength"                # T1
    BLANKING_DISTANCE = "BlankingDistance"                       # T2
    RECEIVE_LENGTH = "ReceiveLength"                             # T3
    TIME_BETWEEN_PINGS = "TimeBetweenPings"                      # T4
    TIME_BETWEEN_BURST_SEQUENCES = "TimeBetweenBurstSequences"   # T5
    NUMBER_PINGS = "NumberPings"     # number of beam sequences per burst
    AVG_INTERVAL = "AvgInterval"
    USER_NUMBER_BEAMS = "UserNumberOfBeams"
    TIMING_CONTROL_REGISTER = "TimingControlRegister"
    POWER_CONTROL_REGISTER = "PowerControlRegister"
    A1_1_SPARE = 'A1_1Spare'
    B0_1_SPARE = 'B0_1Spare'
    B1_1_SPARE = 'B1_1Spare'
    COMPASS_UPDATE_RATE = "CompassUpdateRate"
    COORDINATE_SYSTEM = "CoordinateSystem"
    NUMBER_BINS = "NumberOfBins"      # number of cells
    BIN_LENGTH = "BinLength"          # cell size
    MEASUREMENT_INTERVAL = "MeasurementInterval"
    DEPLOYMENT_NAME = "DeploymentName"
    WRAP_MODE = "WrapMode"
    CLOCK_DEPLOY = "ClockDeploy"      # deployment start time
    DIAGNOSTIC_INTERVAL = "DiagnosticInterval"
    MODE = "Mode"
    ADJUSTMENT_SOUND_SPEED = 'AdjustmentSoundSpeed'
    NUMBER_SAMPLES_DIAGNOSTIC = 'NumberSamplesInDiagMode'
    NUMBER_BEAMS_CELL_DIAGNOSTIC = 'NumberBeamsPerCellInDiagMode'
    NUMBER_PINGS_DIAGNOSTIC = 'NumberPingsInDiagMode'
    MODE_TEST = 'ModeTest'
    ANALOG_INPUT_ADDR = 'AnalogInputAddress'
    SW_VERSION = 'SwVersion'
    USER_1_SPARE = 'User1Spare'
    VELOCITY_ADJ_TABLE = 'VelocityAdjTable'
    COMMENTS = 'Comments'
    WAVE_MEASUREMENT_MODE = 'WaveMeasurementMode'
    DYN_PERCENTAGE_POSITION = 'PercentageForCellPositioning'
    WAVE_TRANSMIT_PULSE = 'WaveTransmitPulse'
    WAVE_BLANKING_DISTANCE = 'WaveBlankingDistance'
    WAVE_CELL_SIZE = 'WaveCellSize'
    NUMBER_DIAG_SAMPLES = 'NumberDiagnosticSamples'
    A1_2_SPARE = 'A1_2Spare'
    B0_2_SPARE = 'B0_2Spare'
    USER_2_SPARE = 'User2Spare'
    ANALOG_OUTPUT_SCALE = 'AnalogOutputScale'
    CORRELATION_THRESHOLD = 'CorrelationThreshold'
    TRANSMIT_PULSE_LENGTH_SECOND_LAG = 'TransmitPulseLengthSecondLag'
    USER_4_SPARE = 'User4Spare'
    QUAL_CONSTANTS = 'StageMatchFilterConstants'
    NUMBER_SAMPLES_PER_BURST = 'NumberSamplesPerBurst'
    USER_3_SPARE = 'User3Spare'


class EngineeringParameter(DriverParameter):
    """
    Driver Paramters (aka, engineering parameters)
    """
    CLOCK_SYNC_INTERVAL = 'ClockSyncInterval'
    ACQUIRE_STATUS_INTERVAL = 'AcquireStatusInterval'

def hw_config_to_dict(input):
    """
    Translate a hardware configuration string into a dictionary, keys being
    from the NortekHardwareConfigDataParticleKey class.
    @param string The incoming string of characters of the correct length.
    Should be the result of a GP command
    @retval A dictionary with the translated values
    @throws SampleException If there is a problem with sample creation
    """
    if str(input[-2:]) == InstrumentPrompts.Z_ACK:
        if len(input) != HW_CONFIG_LEN+2:
            raise SampleException("Invalid input when parsing user config. Got input of size %s with an ACK" % len(input))
    else:
        if len(input) != HW_CONFIG_LEN:
            raise SampleException("Invalid input when parsing user config. Got input of size %s with no ACK" % len(input))

    parsed = {}
    parsed[NortekHardwareConfigDataParticleKey.SERIAL_NUM] = input[4:18]
    parsed[NortekHardwareConfigDataParticleKey.CONFIG] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[18:20])
    parsed[NortekHardwareConfigDataParticleKey.BOARD_FREQUENCY] = NortekProtocolParameterDict.convert_word_to_int(input[20:22])
    parsed[NortekHardwareConfigDataParticleKey.PIC_VERSION] = NortekProtocolParameterDict.convert_word_to_int(input[22:24])
    parsed[NortekHardwareConfigDataParticleKey.HW_REVISION] = NortekProtocolParameterDict.convert_word_to_int(input[24:26])
    parsed[NortekHardwareConfigDataParticleKey.RECORDER_SIZE] = NortekProtocolParameterDict.convert_word_to_int(input[26:28])
    parsed[NortekHardwareConfigDataParticleKey.STATUS] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[28:30])
    parsed[NortekHardwareConfigDataParticleKey.FW_VERSION] = input[42:46]
    parsed[NortekHardwareConfigDataParticleKey.CHECKSUM] = NortekProtocolParameterDict.convert_word_to_int(input[46:48])
    return parsed


class NortekHardwareConfigDataParticleKey(BaseEnum):
    """
    Particle key for the hw config
    """
    SERIAL_NUM = 'instmt_type_serial_number'
    RECORDER_INSTALLED = 'recorder_installed'
    COMPASS_INSTALLED = 'compass_installed'
    BOARD_FREQUENCY = 'board_frequency'
    PIC_VERSION = 'pic_version'
    HW_REVISION = 'hardware_revision'
    RECORDER_SIZE = 'recorder_size'
    VELOCITY_RANGE = 'velocity_range'
    FW_VERSION = 'firmware_version'
    STATUS = 'status'
    CONFIG = 'config'
    CHECKSUM = 'checksum'


class NortekHardwareConfigDataParticle(DataParticle):
    """
    Routine for parsing hardware config data into a data particle structure for the Vector sensor.
    """

    _data_particle_type = NortekDataParticleType.HARDWARE_CONFIG

    def _build_parsed_values(self):
        """
        Take something in the hardware config data sample format and parse it into
        values with appropriate tags.
        """
        working_value = hw_config_to_dict(self.raw_data)

        for key in working_value.keys():
            if None == working_value[key]:
                raise SampleException("No %s value parsed", key)

        working_value[NortekHardwareConfigDataParticleKey.RECORDER_INSTALLED] = working_value[NortekHardwareConfigDataParticleKey.CONFIG][-1]
        working_value[NortekHardwareConfigDataParticleKey.COMPASS_INSTALLED] = working_value[NortekHardwareConfigDataParticleKey.CONFIG][-2]
        working_value[NortekHardwareConfigDataParticleKey.VELOCITY_RANGE] = working_value[NortekHardwareConfigDataParticleKey.STATUS][-1]

        # report values
        result = [{DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.SERIAL_NUM,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.SERIAL_NUM]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.RECORDER_INSTALLED,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.RECORDER_INSTALLED]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.COMPASS_INSTALLED,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.COMPASS_INSTALLED]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.BOARD_FREQUENCY,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.BOARD_FREQUENCY]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.PIC_VERSION,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.PIC_VERSION]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.HW_REVISION,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.HW_REVISION]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.RECORDER_SIZE,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.RECORDER_SIZE]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.VELOCITY_RANGE,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.VELOCITY_RANGE]},
                  {DataParticleKey.VALUE_ID: NortekHardwareConfigDataParticleKey.FW_VERSION,
                   DataParticleKey.VALUE: working_value[NortekHardwareConfigDataParticleKey.FW_VERSION]}]

        calculated_checksum = NortekProtocolParameterDict.calculate_checksum(self.raw_data)
        if working_value[NortekHardwareConfigDataParticleKey.CHECKSUM] != calculated_checksum:
            log.warn("Calculated checksum: %s did not match packet checksum: %s",
                     calculated_checksum, working_value[NortekHardwareConfigDataParticleKey.CHECKSUM])
            self.contents[DataParticleKey.QUALITY_FLAG] = DataParticleValue.CHECKSUM_FAILED

        log.debug('VectorHardwareConfigDataParticle: particle=%s', result)
        return result


def head_config_to_dict(input):
    """
    Translate a head configuration string into a dictionary, keys being
    from the NortekHeadConfigDataParticleKey class.
    @param string The incoming string of characters of the correct length.
    Should be the result of a GH command
    @retval A dictionary with the translated values
    @throws SampleException If there is a problem with sample creation
    """

    if str(input[-2:]) == InstrumentPrompts.Z_ACK:
        if len(input) != HEAD_CONFIG_LEN + 2:
            raise SampleException("Invalid input when parsing user config. Got input of size %s with an ACK" % len(input))
    else:
        if len(input) != HEAD_CONFIG_LEN:
            raise SampleException("Invalid input when parsing user config. Got input of size %s with no ACK" % len(input))

    parsed = {}
    parsed[NortekHeadConfigDataParticleKey.CONFIG] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[4:6])
    parsed[NortekHeadConfigDataParticleKey.HEAD_FREQ] = NortekProtocolParameterDict.convert_word_to_int(input[6:8])
    parsed[NortekHeadConfigDataParticleKey.HEAD_TYPE] = NortekProtocolParameterDict.convert_word_to_int(input[8:10])
    parsed[NortekHeadConfigDataParticleKey.HEAD_SERIAL] = NortekProtocolParameterDict.convert_bytes_to_string(input[10:22])
    parsed[NortekHeadConfigDataParticleKey.SYSTEM_DATA] = base64.b64encode(input[22:198])
    parsed[NortekHeadConfigDataParticleKey.NUM_BEAMS] = NortekProtocolParameterDict.convert_word_to_int(input[220:222])
    parsed[NortekHeadConfigDataParticleKey.CHECKSUM] = NortekProtocolParameterDict.convert_word_to_int(input[222:224])
    return parsed


class NortekHeadConfigDataParticleKey(BaseEnum):
    """
    Particle key for the head config
    """
    PRESSURE_SENSOR = 'pressure_sensor'
    MAG_SENSOR = 'magnetometer_sensor'
    TILT_SENSOR = 'tilt_sensor'
    TILT_SENSOR_MOUNT = 'tilt_sensor_mounting'
    HEAD_FREQ = 'head_frequency'
    HEAD_TYPE = 'head_type'
    HEAD_SERIAL = 'head_serial_number'
    SYSTEM_DATA = 'system_data'
    NUM_BEAMS = 'number_beams'
    CONFIG = 'config'
    CHECKSUM = 'checksum'


class NortekHeadConfigDataParticle(DataParticle):
    """
    Routine for parsing head config data into a data particle structure for the Vector sensor.
    """
    _data_particle_type = NortekDataParticleType.HEAD_CONFIG

    def _build_parsed_values(self):
        """
        Take something in the probe check data sample format and parse it into
        values with appropriate tags.
        @throws SampleException If there is a problem with sample creation
        """
        #match = HEAD_CONFIG_DATA_REGEX.match(self.raw_data)

        #if not match:
        #    raise SampleException("VectorHeadConfigDataParticle: No regex match of parsed sample data: [%s]", self.raw_data)

        working_value = head_config_to_dict(self.raw_data)
        for key in working_value.keys():
            if None == working_value[key]:
                raise SampleException("No %s value parsed", key)

        working_value[NortekHeadConfigDataParticleKey.PRESSURE_SENSOR] = working_value[NortekHeadConfigDataParticleKey.CONFIG][-1]
        working_value[NortekHeadConfigDataParticleKey.MAG_SENSOR] = working_value[NortekHeadConfigDataParticleKey.CONFIG][-2]
        working_value[NortekHeadConfigDataParticleKey.TILT_SENSOR] = working_value[NortekHeadConfigDataParticleKey.CONFIG][-3]
        working_value[NortekHeadConfigDataParticleKey.TILT_SENSOR_MOUNT] = working_value[NortekHeadConfigDataParticleKey.CONFIG][-4]

        # report values
        result = [{DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.PRESSURE_SENSOR,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.PRESSURE_SENSOR]},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.MAG_SENSOR,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.MAG_SENSOR]},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.TILT_SENSOR,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.TILT_SENSOR]},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.TILT_SENSOR_MOUNT,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.TILT_SENSOR_MOUNT]},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.HEAD_FREQ,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.HEAD_FREQ]},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.HEAD_TYPE,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.HEAD_TYPE]},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.HEAD_SERIAL,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.HEAD_SERIAL]},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.SYSTEM_DATA,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.SYSTEM_DATA],
                   DataParticleKey.BINARY: True},
                  {DataParticleKey.VALUE_ID: NortekHeadConfigDataParticleKey.NUM_BEAMS,
                   DataParticleKey.VALUE: working_value[NortekHeadConfigDataParticleKey.NUM_BEAMS]}]

        calculated_checksum = NortekProtocolParameterDict.calculate_checksum(self.raw_data)
        if working_value[NortekHeadConfigDataParticleKey.CHECKSUM] != calculated_checksum:
            log.warn("Calculated checksum: %s did not match packet checksum: %s",
                     calculated_checksum, working_value[NortekHeadConfigDataParticleKey.CHECKSUM])
            self.contents[DataParticleKey.QUALITY_FLAG] = DataParticleValue.CHECKSUM_FAILED

        log.debug('VectorHeadConfigDataParticle: particle=%s', result)
        return result


def user_config_to_dict(input):
    """
    Translate a user configuration string into a dictionary, keys being
    from the NortekUserConfigDataParticleKey class.
    @param string The incoming string of characters of the correct length.
    Should be the result of a GC command
    @retval A dictionary with the translated values
    @throws SampleException If there is a problem with sample creation
    """
    # Trim an ACK off the end if we care
    if str(input[-2:]) == InstrumentPrompts.Z_ACK:
        if (len(input) != USER_CONFIG_LEN+2):
            raise SampleException("Invalid input when parsing user config. Got input of size %s with an ACK" % len(input))
    else:
        if (len(input) != USER_CONFIG_LEN):
            raise SampleException("Invalid input when parsing user config. Got input of size %s with no ACK" % len(input))

    parsed = {}
    parsed[NortekUserConfigDataParticleKey.TX_LENGTH] = NortekProtocolParameterDict.convert_word_to_int(input[4:6])
    parsed[NortekUserConfigDataParticleKey.BLANK_DIST] = NortekProtocolParameterDict.convert_word_to_int(input[6:8])
    parsed[NortekUserConfigDataParticleKey.RX_LENGTH] = NortekProtocolParameterDict.convert_word_to_int(input[8:10])
    parsed[NortekUserConfigDataParticleKey.TIME_BETWEEN_PINGS] = NortekProtocolParameterDict.convert_word_to_int(input[10:12])
    parsed[NortekUserConfigDataParticleKey.TIME_BETWEEN_BURSTS] = NortekProtocolParameterDict.convert_word_to_int(input[12:14])
    parsed[NortekUserConfigDataParticleKey.NUM_PINGS] = NortekProtocolParameterDict.convert_word_to_int(input[14:16])
    parsed[NortekUserConfigDataParticleKey.AVG_INTERVAL] = NortekProtocolParameterDict.convert_word_to_int(input[16:18])
    parsed[NortekUserConfigDataParticleKey.NUM_BEAMS] = NortekProtocolParameterDict.convert_word_to_int(input[18:20])
    parsed[NortekUserConfigDataParticleKey.TCR] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[20:22])
    parsed[NortekUserConfigDataParticleKey.PCR] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[22:24])
    parsed[NortekUserConfigDataParticleKey.COMPASS_UPDATE_RATE] = NortekProtocolParameterDict.convert_word_to_int(input[30:32])
    parsed[NortekUserConfigDataParticleKey.COORDINATE_SYSTEM] = NortekProtocolParameterDict.convert_word_to_int(input[32:34])
    parsed[NortekUserConfigDataParticleKey.NUM_CELLS] = NortekProtocolParameterDict.convert_word_to_int(input[34:36])
    parsed[NortekUserConfigDataParticleKey.CELL_SIZE] = NortekProtocolParameterDict.convert_word_to_int(input[36:38])
    parsed[NortekUserConfigDataParticleKey.MEASUREMENT_INTERVAL] = NortekProtocolParameterDict.convert_word_to_int(input[38:40])
    parsed[NortekUserConfigDataParticleKey.DEPLOYMENT_NAME] = NortekProtocolParameterDict.convert_bytes_to_string(input[40:46])
    parsed[NortekUserConfigDataParticleKey.WRAP_MODE] = NortekProtocolParameterDict.convert_word_to_int(input[46:48])
    parsed[NortekUserConfigDataParticleKey.DEPLOY_START_TIME] = NortekProtocolParameterDict.convert_words_to_datetime(input[48:54])
    parsed[NortekUserConfigDataParticleKey.DIAG_INTERVAL] = NortekProtocolParameterDict.convert_double_word_to_int(input[54:58])
    parsed[NortekUserConfigDataParticleKey.MODE] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[58:60])
    parsed[NortekUserConfigDataParticleKey.SOUND_SPEED_ADJUST] = NortekProtocolParameterDict.convert_word_to_int(input[60:62])
    parsed[NortekUserConfigDataParticleKey.NUM_DIAG_SAMPLES] = NortekProtocolParameterDict.convert_word_to_int(input[62:64])
    parsed[NortekUserConfigDataParticleKey.NUM_BEAMS_PER_CELL] = NortekProtocolParameterDict.convert_word_to_int(input[64:66])
    parsed[NortekUserConfigDataParticleKey.NUM_PINGS_DIAG] = NortekProtocolParameterDict.convert_word_to_int(input[66:68])
    parsed[NortekUserConfigDataParticleKey.MODE_TEST] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[68:70])
    parsed[NortekUserConfigDataParticleKey.ANALOG_INPUT_ADDR] = NortekProtocolParameterDict.convert_word_to_int(input[70:72])
    parsed[NortekUserConfigDataParticleKey.SW_VER] = NortekProtocolParameterDict.convert_word_to_int(input[72:74])
    parsed[NortekUserConfigDataParticleKey.VELOCITY_ADJ_FACTOR] = base64.b64encode(input[76:256])
    parsed[NortekUserConfigDataParticleKey.FILE_COMMENTS] = NortekProtocolParameterDict.convert_bytes_to_string(input[256:436])
    parsed[NortekUserConfigDataParticleKey.WAVE_MODE] = NortekProtocolParameterDict.convert_bytes_to_bit_field(input[436:438])
    parsed[NortekUserConfigDataParticleKey.PERCENT_WAVE_CELL_POS] = NortekProtocolParameterDict.convert_word_to_int(input[438:440])
    parsed[NortekUserConfigDataParticleKey.WAVE_TX_PULSE] = NortekProtocolParameterDict.convert_word_to_int(input[440:442])
    parsed[NortekUserConfigDataParticleKey.FIX_WAVE_BLANK_DIST] = NortekProtocolParameterDict.convert_word_to_int(input[442:444])
    parsed[NortekUserConfigDataParticleKey.WAVE_CELL_SIZE] = NortekProtocolParameterDict.convert_word_to_int(input[444:446])
    parsed[NortekUserConfigDataParticleKey.NUM_DIAG_PER_WAVE] = NortekProtocolParameterDict.convert_word_to_int(input[446:448])
    parsed[NortekUserConfigDataParticleKey.NUM_SAMPLE_PER_BURST] = NortekProtocolParameterDict.convert_word_to_int(input[452:454])
    parsed[NortekUserConfigDataParticleKey.ANALOG_SCALE_FACTOR] = NortekProtocolParameterDict.convert_word_to_int(input[456:458])
    parsed[NortekUserConfigDataParticleKey.CORRELATION_THRS] = NortekProtocolParameterDict.convert_word_to_int(input[458:460])
    parsed[NortekUserConfigDataParticleKey.TX_PULSE_LEN_2ND] = NortekProtocolParameterDict.convert_word_to_int(input[462:464])
    parsed[NortekUserConfigDataParticleKey.FILTER_CONSTANTS] = base64.b64encode(input[494:510])
    parsed[NortekUserConfigDataParticleKey.CHECKSUM] = NortekProtocolParameterDict.convert_word_to_int(input[510:512])

    return parsed


class NortekUserConfigDataParticleKey(BaseEnum):
    """
    User Config particle keys
    """
    TX_LENGTH = 'transmit_pulse_length'
    BLANK_DIST = 'blanking_distance'
    RX_LENGTH = 'receive_length'
    TIME_BETWEEN_PINGS = 'time_between_pings'
    TIME_BETWEEN_BURSTS = 'time_between_bursts'
    NUM_PINGS = 'number_pings'
    AVG_INTERVAL = 'average_interval'
    NUM_BEAMS = 'number_beams'
    PROFILE_TYPE = 'profile_type'
    MODE_TYPE = 'mode_type'
    TCR = 'tcr'
    PCR = 'pcr'
    POWER_TCM1 = 'power_level_tcm1'
    POWER_TCM2 = 'power_level_tcm2'
    SYNC_OUT_POSITION = 'sync_out_position'
    SAMPLE_ON_SYNC = 'sample_on_sync'
    START_ON_SYNC = 'start_on_sync'
    POWER_PCR1 = 'power_level_pcr1'
    POWER_PCR2 = 'power_level_pcr2'
    COMPASS_UPDATE_RATE = 'compass_update_rate'
    COORDINATE_SYSTEM = 'coordinate_system'
    NUM_CELLS = 'number_cells'
    CELL_SIZE = 'cell_size'
    MEASUREMENT_INTERVAL = 'measurement_interval'
    DEPLOYMENT_NAME = 'deployment_name'
    WRAP_MODE = 'wrap_moder'
    DEPLOY_START_TIME = 'deployment_start_time'
    DIAG_INTERVAL = 'diagnostics_interval'
    MODE = 'mode'
    USE_SPEC_SOUND_SPEED = 'use_specified_sound_speed'
    DIAG_MODE_ON = 'diagnostics_mode_enable'
    ANALOG_OUTPUT_ON = 'analog_output_enable'
    OUTPUT_FORMAT = 'output_format_nortek'
    SCALING = 'scaling'
    SERIAL_OUT_ON = 'serial_output_enable'
    STAGE_ON = 'stage_enable'
    ANALOG_POWER_OUTPUT = 'analog_power_output'
    SOUND_SPEED_ADJUST = 'sound_speed_adjust_factor'
    NUM_DIAG_SAMPLES = 'number_diagnostics_samples'
    NUM_BEAMS_PER_CELL = 'number_beams_per_cell'
    NUM_PINGS_DIAG = 'number_pings_diagnostic'
    MODE_TEST = 'mode_test'
    USE_DSP_FILTER = 'use_dsp_filter'
    FILTER_DATA_OUTPUT = 'filter_data_output'
    ANALOG_INPUT_ADDR = 'analog_input_address'
    SW_VER = 'software_version'
    VELOCITY_ADJ_FACTOR = 'velocity_adjustment_factor'
    FILE_COMMENTS = 'file_comments'
    WAVE_MODE = 'wave_mode'
    WAVE_DATA_RATE = 'wave_data_rate'
    WAVE_CELL_POS = 'wave_cell_pos'
    DYNAMIC_POS_TYPE = 'dynamic_position_type'
    PERCENT_WAVE_CELL_POS = 'percent_wave_cell_position'
    WAVE_TX_PULSE = 'wave_transmit_pulse'
    FIX_WAVE_BLANK_DIST = 'fixed_wave_blanking_distance'
    WAVE_CELL_SIZE = 'wave_measurement_cell_size'
    NUM_DIAG_PER_WAVE = 'number_diagnostics_per_wave'
    NUM_SAMPLE_PER_BURST = 'number_samples_per_burst'
    ANALOG_SCALE_FACTOR = 'analog_scale_factor'
    CORRELATION_THRS = 'correlation_threshold'
    TX_PULSE_LEN_2ND = 'transmit_pulse_length_2nd'
    FILTER_CONSTANTS = 'filter_constants'
    CHECKSUM = 'checksum'


class NortekUserConfigDataParticle(DataParticle):
    """
    Routine for parsing head config data into a data particle structure for the Vector sensor.
    """

    _data_particle_type = NortekDataParticleType.USER_CONFIG

    def _build_parsed_values(self):
        """
        Take something in the probe check data sample format and parse it into
        values with appropriate tags.
        @throws SampleException If there is a problem with sample creation
        """
        working_value = user_config_to_dict(self.raw_data)
        for key in working_value.keys():
            if None == working_value[key]:
                raise SampleException("No %s value parsed", key)

        # Fill in the bitfields
        working_value[NortekUserConfigDataParticleKey.PROFILE_TYPE] = working_value[NortekUserConfigDataParticleKey.TCR][-2]
        working_value[NortekUserConfigDataParticleKey.MODE_TYPE] = working_value[NortekUserConfigDataParticleKey.TCR][-3]
        working_value[NortekUserConfigDataParticleKey.POWER_TCM1] = working_value[NortekUserConfigDataParticleKey.TCR][-6]
        working_value[NortekUserConfigDataParticleKey.POWER_TCM2] = working_value[NortekUserConfigDataParticleKey.TCR][-7]
        working_value[NortekUserConfigDataParticleKey.SYNC_OUT_POSITION] = working_value[NortekUserConfigDataParticleKey.TCR][-8]
        working_value[NortekUserConfigDataParticleKey.SAMPLE_ON_SYNC] = working_value[NortekUserConfigDataParticleKey.TCR][-9]
        working_value[NortekUserConfigDataParticleKey.START_ON_SYNC] = working_value[NortekUserConfigDataParticleKey.TCR][-10]

        working_value[NortekUserConfigDataParticleKey.POWER_PCR1] = working_value[NortekUserConfigDataParticleKey.PCR][-6]
        working_value[NortekUserConfigDataParticleKey.POWER_PCR2] = working_value[NortekUserConfigDataParticleKey.PCR][-7]

        working_value[NortekUserConfigDataParticleKey.USE_SPEC_SOUND_SPEED] = bool(working_value[NortekUserConfigDataParticleKey.MODE][-1])
        working_value[NortekUserConfigDataParticleKey.DIAG_MODE_ON] = bool(working_value[NortekUserConfigDataParticleKey.MODE][-2])
        working_value[NortekUserConfigDataParticleKey.ANALOG_OUTPUT_ON] = bool(working_value[NortekUserConfigDataParticleKey.MODE][-3])
        working_value[NortekUserConfigDataParticleKey.OUTPUT_FORMAT] = working_value[NortekUserConfigDataParticleKey.MODE][-4]
        working_value[NortekUserConfigDataParticleKey.SCALING] = working_value[NortekUserConfigDataParticleKey.MODE][-5]
        working_value[NortekUserConfigDataParticleKey.SERIAL_OUT_ON] = bool(working_value[NortekUserConfigDataParticleKey.MODE][-6])
        working_value[NortekUserConfigDataParticleKey.STAGE_ON] = bool(working_value[NortekUserConfigDataParticleKey.MODE][-8])
        working_value[NortekUserConfigDataParticleKey.ANALOG_POWER_OUTPUT] = bool(working_value[NortekUserConfigDataParticleKey.MODE][-9])

        working_value[NortekUserConfigDataParticleKey.USE_DSP_FILTER] = bool(working_value[NortekUserConfigDataParticleKey.MODE_TEST][-1])
        working_value[NortekUserConfigDataParticleKey.FILTER_DATA_OUTPUT] = working_value[NortekUserConfigDataParticleKey.MODE_TEST][-2]

        working_value[NortekUserConfigDataParticleKey.WAVE_DATA_RATE] = working_value[NortekUserConfigDataParticleKey.WAVE_MODE][-1]
        working_value[NortekUserConfigDataParticleKey.WAVE_CELL_POS] = working_value[NortekUserConfigDataParticleKey.WAVE_MODE][-2]
        working_value[NortekUserConfigDataParticleKey.DYNAMIC_POS_TYPE] = working_value[NortekUserConfigDataParticleKey.WAVE_MODE][-3]

        for key in working_value.keys():
            if None == working_value[key]:
                raise SampleException("No %s value parsed", key)

        # report values
        result = [{DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.TX_LENGTH,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.TX_LENGTH]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.BLANK_DIST,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.BLANK_DIST]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.RX_LENGTH,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.RX_LENGTH]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.TIME_BETWEEN_PINGS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.TIME_BETWEEN_PINGS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.TIME_BETWEEN_BURSTS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.TIME_BETWEEN_BURSTS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_PINGS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_PINGS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.AVG_INTERVAL,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.AVG_INTERVAL]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_BEAMS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_BEAMS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.PROFILE_TYPE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.PROFILE_TYPE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.MODE_TYPE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.MODE_TYPE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.POWER_TCM1,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.POWER_TCM1]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.POWER_TCM2,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.POWER_TCM2]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.SYNC_OUT_POSITION,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.SYNC_OUT_POSITION]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.SAMPLE_ON_SYNC,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.SAMPLE_ON_SYNC]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.START_ON_SYNC,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.START_ON_SYNC]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.POWER_PCR1,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.POWER_PCR1]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.POWER_PCR2,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.POWER_PCR2]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.COMPASS_UPDATE_RATE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.COMPASS_UPDATE_RATE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.COORDINATE_SYSTEM,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.COORDINATE_SYSTEM]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_CELLS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_CELLS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.CELL_SIZE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.CELL_SIZE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.MEASUREMENT_INTERVAL,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.MEASUREMENT_INTERVAL]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.DEPLOYMENT_NAME,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.DEPLOYMENT_NAME]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.WRAP_MODE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.WRAP_MODE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.DEPLOY_START_TIME,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.DEPLOY_START_TIME]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.DIAG_INTERVAL,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.DIAG_INTERVAL]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.USE_SPEC_SOUND_SPEED,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.USE_SPEC_SOUND_SPEED]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.DIAG_MODE_ON,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.DIAG_MODE_ON]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.ANALOG_OUTPUT_ON,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.ANALOG_OUTPUT_ON]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.OUTPUT_FORMAT,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.OUTPUT_FORMAT]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.SCALING,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.SCALING]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.SERIAL_OUT_ON,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.SERIAL_OUT_ON]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.STAGE_ON,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.STAGE_ON]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.ANALOG_POWER_OUTPUT,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.ANALOG_POWER_OUTPUT]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.SOUND_SPEED_ADJUST,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.SOUND_SPEED_ADJUST]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_DIAG_SAMPLES,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_DIAG_SAMPLES]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_BEAMS_PER_CELL,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_BEAMS_PER_CELL]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_PINGS_DIAG,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_PINGS_DIAG]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.USE_DSP_FILTER,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.USE_DSP_FILTER]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.FILTER_DATA_OUTPUT,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.FILTER_DATA_OUTPUT]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.ANALOG_INPUT_ADDR,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.ANALOG_INPUT_ADDR]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.SW_VER,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.SW_VER]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.VELOCITY_ADJ_FACTOR,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.VELOCITY_ADJ_FACTOR]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.FILE_COMMENTS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.FILE_COMMENTS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.WAVE_DATA_RATE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.WAVE_DATA_RATE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.WAVE_CELL_POS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.WAVE_CELL_POS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.DYNAMIC_POS_TYPE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.DYNAMIC_POS_TYPE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.PERCENT_WAVE_CELL_POS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.PERCENT_WAVE_CELL_POS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.WAVE_TX_PULSE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.WAVE_TX_PULSE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.FIX_WAVE_BLANK_DIST,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.FIX_WAVE_BLANK_DIST]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.WAVE_CELL_SIZE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.WAVE_CELL_SIZE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_DIAG_PER_WAVE,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_DIAG_PER_WAVE]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.NUM_SAMPLE_PER_BURST,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.NUM_SAMPLE_PER_BURST]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.ANALOG_SCALE_FACTOR,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.ANALOG_SCALE_FACTOR]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.CORRELATION_THRS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.CORRELATION_THRS]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.TX_PULSE_LEN_2ND,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.TX_PULSE_LEN_2ND]},
                  {DataParticleKey.VALUE_ID: NortekUserConfigDataParticleKey.FILTER_CONSTANTS,
                   DataParticleKey.VALUE: working_value[NortekUserConfigDataParticleKey.FILTER_CONSTANTS]},
                  ]

        calculated_checksum = NortekProtocolParameterDict.calculate_checksum(self.raw_data)
        if working_value[NortekUserConfigDataParticleKey.CHECKSUM] != calculated_checksum:
            log.warn("Calculated checksum: %s did not match packet checksum: %s",
                     calculated_checksum, working_value[NortekUserConfigDataParticleKey.CHECKSUM])
            self.contents[DataParticleKey.QUALITY_FLAG] = DataParticleValue.CHECKSUM_FAILED

        log.debug('VectorUserConfigDataParticle: particle=%s', result)
        return result


class NortekEngClockDataParticleKey(BaseEnum):
    """
    Particles for the clock data
    """
    DATE_TIME_ARRAY = "date_time_array"
    DATE_TIME_STAMP = "date_time_stamp"


class NortekEngClockDataParticle(DataParticle):
    """
    Routine for parsing clock engineering data into a data particle structure
    for the Vector sensor.
    """
    _data_particle_type = NortekDataParticleType.CLOCK

    def _build_parsed_values(self):
        """
        Take something in the clock engineering data sample format and parse it
        into values with appropriate tags.
        @throws SampleException If there is a problem with sample creation
        """
        match = CLOCK_DATA_REGEX.match(self.raw_data)

        if not match:
            raise SampleException("NortekEngClockDataParticle: No regex match of parsed sample data: [%s]" %
                                  self.raw_data)

        date_time_array = [int((match.group(1)).encode("hex"), 16),
                           int((match.group(2)).encode("hex"), 16),
                           int((match.group(3)).encode("hex"), 16),
                           int((match.group(4)).encode("hex"), 16),
                           int((match.group(5)).encode("hex"), 16),
                           int((match.group(6)).encode("hex"), 16)]

        if None == date_time_array:
            raise SampleException("No date/time array value parsed")

        # report values
        result = [{DataParticleKey.VALUE_ID: NortekEngClockDataParticleKey.DATE_TIME_ARRAY,
                   DataParticleKey.VALUE: date_time_array}]

        log.debug('NortekEngClockDataParticle: particle=%s', result)
        return result


class NortekEngBatteryDataParticleKey(BaseEnum):
    """
    Particles for the battery data
    """
    BATTERY_VOLTAGE = "battery_voltage"


class NortekEngBatteryDataParticle(DataParticle):
    """
    Routine for parsing battery engineering data into a data particle
    structure for the Vector sensor.
    """
    _data_particle_type = NortekDataParticleType.BATTERY

    def _build_parsed_values(self):
        """
        Take something in the battery engineering data sample format and parse
        it into values with appropriate tags.
        @throws SampleException If there is a problem with sample creation
        """
        match = BATTERY_DATA_REGEX.match(self.raw_data)

        if not match:
            raise SampleException("NortekEngBatteryDataParticle: No regex match of parsed sample data: [%s]" % self.raw_data)

        # Calculate value
        battery_voltage = NortekProtocolParameterDict.convert_word_to_int(match.group(1))

        if None == battery_voltage:
            raise SampleException("No battery_voltage value parsed")

        # report values
        result = [{DataParticleKey.VALUE_ID: NortekEngBatteryDataParticleKey.BATTERY_VOLTAGE,
                   DataParticleKey.VALUE: battery_voltage}]

        log.debug('NortekEngBatteryDataParticle: particle=%s', result)
        return result


class NortekEngIdDataParticleKey(BaseEnum):
    ID = "identification_string"


class NortekEngIdDataParticle(DataParticle):
    """
    Routine for parsing id engineering data into a data particle
    structure for the Vector sensor.
    """
    _data_particle_type = NortekDataParticleType.ID_STRING

    def _build_parsed_values(self):
        """
        Take something in the battery engineering data sample format and parse
        it into values with appropriate tags.
        @throws SampleException If there is a problem with sample creation
        """
        match = ID_DATA_REGEX.match(self.raw_data)

        if not match:
            raise SampleException("NortekEngIdDataParticle: No regex match of parsed sample data: [%s]", self.raw_data)

        id_str = NortekProtocolParameterDict.convert_bytes_to_string(match.group(1))

        if None == id_str:
            raise SampleException("No ID value parsed")

        # report values
        result = [{DataParticleKey.VALUE_ID: NortekEngIdDataParticleKey.ID,
                   DataParticleKey.VALUE: id_str}]

        log.debug('NortekEngIdDataParticle: particle=%s', result)
        return result


###############################################################################
# Param dictionary helpers
###############################################################################
class NortekParameterDictVal(RegexParameter):

    def update(self, input, **kwargs):
        """
        Attempt to update a parameter value. If the input string matches the
        value regex, extract and update the dictionary value.
        @param input A string possibly containing the parameter value.
        @retval True if an update was successful, False otherwise.
        """
        init_value = kwargs.get('init_value', False)
        match = self.regex.match(input)
        if match:
            log.debug('NortekDictVal.update(): match=<%s>, init_value=%s', match.group(1).encode('hex'), init_value)
            value = self.f_getval(match)
            if init_value:
                self.description.init_value = value
            else:
                self.value.set_value(value)
            if isinstance(value, int):
                log.debug('NortekParameterDictVal.update(): updated parameter %s=<%d>', self.name, value)
            else:
                log.debug('NortekParameterDictVal.update(): updated parameter %s=\"%s\" <%s>', self.name,
                          value, str(self.value.get_value()).encode('hex'))
            return True
        else:
            log.debug('NortekParameterDictVal.update(): failed to update parameter %s', self.name)
            log.debug('input=%s', input.encode('hex'))
            log.debug('regex=%s', str(self.regex))
            return False


class NortekProtocolParameterDict(ProtocolParameterDict):

    def update(self, input, target_params=None, **kwargs):
        """
        Update the dictionary with a line input. Iterate through all objects
        and attempt to match and update a parameter. Only updates the first
        match encountered. If we pass in a target params list then we will
        only iterate through those allowing us to limit upstate to only specific
        parameters.
        @param input A string to match to a dictionary object.
        @param target_params a name, or list of names to limit the scope of
        the update.
        @retval The name that was successfully updated, None if not updated
        @raise InstrumentParameterException on invalid target prams
        @raise KeyError on invalid parameter name
        """
        log.debug("update input: %s", input)
        found = False

        if(target_params and isinstance(target_params, str)):
            params = [target_params]
        elif(target_params and isinstance(target_params, list)):
            params = target_params
        elif(target_params == None):
            params = self._param_dict.keys()
        else:
            raise InstrumentParameterException("invalid target_params, must be name or list")

        for name in params:
            log.debug("update param dict name: %s", name)
            val = self._param_dict[name]
            if val.update(input, **kwargs):
                found = True
        return found

    @staticmethod
    def convert_to_raw_value(param_name, initial_value):
        """
        Convert COMMENTS, DEPLOYMENT_NAME, QUAL_CONSTANTS, VELOCITY_ADJ_TABLE,
        and CLOCK_DEPLOY back to their instrument-ready binary representation
        despite them being stored in an ION-friendly not-raw-binary format.
        @param initial_value The value that is being converted
        @retval The raw, instrument-binary value for that name. If the value would
        already be instrument-level coming  out of the param dict, there is
        no change
        """
        if param_name == Parameter.COMMENTS:
            return initial_value.ljust(180, "\x00")
        if param_name == Parameter.DEPLOYMENT_NAME:
            return initial_value.ljust(6, "\x00")
        if param_name == Parameter.QUAL_CONSTANTS:
            return base64.b64decode(initial_value.get_value())
        if param_name == Parameter.VELOCITY_ADJ_TABLE:
            log.debug("TABLE = %r", base64.b64decode(initial_value.get_value()))
            return base64.b64decode(initial_value.get_value())
        if param_name == Parameter.CLOCK_DEPLOY:
            return NortekProtocolParameterDict.convert_datetime_to_words(initial_value.get_value())

        return initial_value

    def get_config(self):
        """
        Retrieve the configuration (all key values not ending in 'Spare').
        """
        config = {}
        for (key, val) in self._param_dict.iteritems():
            log.debug("Getting configuration key [%s] with value: [%s]", key, val.value.value)
            if not key.endswith('Spare'):
                config[key] = val.get_value()
        return config

    def set_from_value(self, name, value):
        """
        Set a parameter value in the dictionary.
        @param name The parameter name.
        @param value The parameter value.
        @raises KeyError if the name is invalid.
        """
        log.debug("NortekProtocolParameterDict.set_from_value(): name=%s, value=%s", name, value)

        retval = False

        if not name in self._param_dict:
            raise InstrumentParameterException('Unable to set parameter %s to %s: parameter %s not an dictionary' % (name, value, name))

        if ((self._param_dict[name].value.f_format == NortekProtocolParameterDict.word_to_string) or
             (self._param_dict[name].value.f_format == NortekProtocolParameterDict.double_word_to_string)):
            if not isinstance(value, int):
                raise InstrumentParameterException('Unable to set parameter %s to %s: value not an integer' % (name, value))
        # else:
        #    if not isinstance(value, str):
        #        raise InstrumentParameterException('Unable to set parameter %s to %s: value not a string' %(name, value))

        if self._param_dict[name].description.visibility == ParameterDictVisibility.READ_ONLY:
            raise ReadOnlyException('Unable to set parameter %s to %s: parameter %s is read only' % (name, value, name))

        if value != self._param_dict[name].value.get_value():
            log.debug("old value: %s, new value: %s", self._param_dict[name].value.get_value(), value)
            retval = True
        self._param_dict[name].value.set_value(value)

        return retval

    @staticmethod
    def word_to_string(value):
        """
        Converts a word into a string field
        """
        #log.debug("CONVERTEING word_to_string")
        low_byte = value & 0xff
        high_byte = (value & 0xff00) >> 8
        return chr(low_byte) + chr(high_byte)

    @staticmethod
    def convert_word_to_int(word):
        """
        Converts a word into an integer field
        """
        #log.debug("CONVERTING convert_word_to_int")
        if len(word) != 2:
            raise SampleException("Invalid number of bytes in word input! Found %s with input %s" % len(word))

        if word is None:
            log.debug("THERE IS NO VALUE")

        low_byte = ord(word[0])
        high_byte = 0x100 * ord(word[1])
        #log.debug('low = %s, high=%s, combined = %s', low_byte, high_byte, low_byte+high_byte)
        return low_byte + high_byte

    @staticmethod
    def double_word_to_string(value):
        """
        Converts 2 words into a string field
        """
        result = NortekProtocolParameterDict.word_to_string(value & 0xffff)
        result += NortekProtocolParameterDict.word_to_string((value & 0xffff0000) >> 16)
        return result

    @staticmethod
    def convert_double_word_to_int(dword):
        """
        Converts 2 words into an integer field
        """
        if len(dword) != 4:
            raise SampleException("Invalid number of bytes in double word input! Found %s" % len(dword))
        low_word = NortekProtocolParameterDict.convert_word_to_int(dword[0:2])
        high_word = NortekProtocolParameterDict.convert_word_to_int(dword[2:4])
        #log.debug('dw=%s, lw=%d, hw=%d, v=%d' %(dword.encode('hex'), low_word, high_word, low_word + (0x10000 * high_word)))
        return low_word + (0x10000 * high_word)

    @staticmethod
    def convert_bytes_to_bit_field(bytes):
        """
        Convert bytes to a bit field, reversing bytes in the process.
        ie ['\x05', '\x01'] becomes [0, 0, 0, 1, 0, 1, 0, 1]
        @param bytes an array of string literal bytes.
        @retval an list of 1 or 0 in order
        """
        byte_list = list(bytes)
        byte_list.reverse()
        result = []
        for byte in byte_list:
            bin_string = bin(ord(byte))[2:].rjust(8, '0')
            result.extend([int(x) for x in list(bin_string)])
        #log.debug("Returning a bitfield of %s for input string: [%s]", result, bytes)
        return result

    @staticmethod
    def convert_words_to_datetime(bytes):
        """
        Convert block of 6 words into a date/time structure for the
        instrument family
        @param bytes 6 bytes
        @retval An array of 6 ints corresponding to the date/time structure
        @raise SampleException If the date/time cannot be found
        """
        log.debug("Converting date/time bytes (ord values): %s", map(ord, bytes))
        if len(bytes) != 6:
            raise SampleException("Invalid number of bytes in input! Found %s" % len(bytes))

        list = NortekProtocolParameterDict.convert_to_array(bytes, 1)
        for i in range(0, len(list)):
            list[i] = int(list[i].encode("hex"), 16)

        return list

    @staticmethod
    def convert_datetime_to_words(int_array):
        """
        Convert array if integers into a block of 6 words that could be fed
        back to the instrument as a timestamp. The 6 array probably came from
        convert_words_to_datetime in the first place.
        @param int_array An array of 6 hex values corresponding to a vector
        date/time stamp.
        @retval A string of 6 binary characters
        """
        if len(int_array) != 6:
            raise SampleException("Invalid number of bytes in date/time input! Found %s" % len(int_array))

        list = [chr(int(str(n), 16)) for n in int_array]
        return "".join(list)

    @staticmethod
    def convert_to_array(bytes, item_size):
        """
        Convert the byte stream into a array with each element being
        item_size bytes. ie '\x01\x02\x03\x04' with item_size 2 becomes
        ['\x01\x02', '\x03\x04']
        @param item_size the size in bytes to make each element
        @retval An array with elements of the correct size
        @raise SampleException if there are problems unpacking the bytes or
        fitting them all in evenly.
        """
        length = len(bytes)
        if length % item_size != 0:
            raise SampleException("Uneven number of bytes for size %s" % item_size)
        l = list(bytes)
        result = []
        for i in range(0, length, item_size):
            result.append("".join(l[i:i+item_size]))
        return result

    @staticmethod
    def calculate_checksum(input, length=None):
        """
        Calculate the checksum
        """
        log.debug("calculate_checksum: input=%s, length=%s", input.encode('hex'), length)
        calculated_checksum = CHECK_SUM_SEED
        if length is None:
            length = len(input)

        for word_index in range(0, length-2, 2):

            word_value = NortekProtocolParameterDict.convert_word_to_int(input[word_index:word_index+2])

            calculated_checksum = (calculated_checksum + word_value) % 0x10000
            #log.debug('word_index = %s, word_value = %r, checksum = %r', word_index, hex(word_value), hex(calculated_checksum + word_value))
            #log.debug('w_i=%d, c_c=%d', word_index, calculated_checksum)
        return calculated_checksum

    @staticmethod
    def convert_bytes_to_string(bytes_in):
        """
        Convert a list of bytes into a string, remove trailing nulls
        ie. ['\x65', '\x66'] turns into "ef"
        @param bytes_in The byte list to take in
        @retval The string to return
        """
        ba = bytearray(bytes_in)
        return str(ba).split('\x00', 1)[0]

    @staticmethod
    def convert_time(response):
        """
        Converts the timestamp in hex to D:M:YYYY HH:MM:SS
        """
        t = str(response[2].encode('hex'))  # get day
        t += '/' + str(response[5].encode('hex'))  # get month
        t += '/20' + str(response[4].encode('hex'))  # get year
        t += ' ' + str(response[3].encode('hex'))  # get hours
        t += ':' + str(response[0].encode('hex'))  # get minutes
        t += ':' + str(response[1].encode('hex'))  # get seconds
        return t


###############################################################################
# Driver
###############################################################################
class NortekInstrumentDriver(SingleConnectionInstrumentDriver):
    """
    Base class for all seabird instrument drivers.
    """
    def __init__(self, evt_callback):
        """
        Driver constructor.
        @param evt_callback Driver process event callback.
        """
        #Construct superclass.
        SingleConnectionInstrumentDriver.__init__(self, evt_callback)

    def _build_protocol(self):
        """
        Construct the driver protocol state machine.
        """
        self._protocol = NortekInstrumentProtocol(InstrumentPrompts, NEWLINE, self._driver_event)

    def get_resource_params(self):
        """
        Return list of device parameters available.
        """
        return Parameter.list()

    # def apply_startup_params(self):
    #     """
    #     Over-ridden to add the 'NotUserRequested' keyed parameter to allow writing to read-only params
    #     Apply the startup values previously stored in the protocol to
    #     the running config of the live instrument. The startup values are the
    #     values that are (1) marked as startup parameters and are (2) the "best"
    #     value to use at startup. Preference is given to the previously-set init
    #     value, then the default value, then the currently used value.
    #
    #     This default method assumes a dict of parameter name and value for
    #     the configuration.
    #     @raise InstrumentParameterException If the config cannot be applied
    #     """
    #     config = self._protocol.get_startup_config()
    #     log.debug("Startup config to be applied: %s", config)
    #
    #     if not isinstance(config, dict):
    #         raise InstrumentParameterException("Incompatible initialization parameters")
    #
    #
    #     self.set_resource(config, NotUserRequested=True)
    # def restore_direct_access_params(self, config):
    #     """
    #     Over-ridden to add the 'NotUserRequested' keyed parameter to allow writing to read-only params
    #     Restore the correct values out of the full config that is given when
    #     returning from direct access. By default, this takes a simple dict of
    #     param name and value. Override this class as needed as it makes some
    #     simple assumptions about how your instrument sets things.
    #
    #     @param config The configuration that was previously saved (presumably
    #     to disk somewhere by the driver that is working with this protocol)
    #     """
    #     vals = {}
    #     # for each parameter that is read only, restore
    #     da_params = self._protocol.get_direct_access_params()
    #     for param in da_params:
    #         vals[param] = config[param]
    #
    #     self.set_resource(vals, NotUserRequested=True)


###############################################################################
# Protocol
###############################################################################

class NortekInstrumentProtocol(CommandResponseInstrumentProtocol):
    """
    Instrument protocol class for seabird driver.
    Subclasses CommandResponseInstrumentProtocol
    """

    UserParameters = [
        # user configuration
        Parameter.TRANSMIT_PULSE_LENGTH,
        Parameter.BLANKING_DISTANCE,
        Parameter.RECEIVE_LENGTH,
        Parameter.TIME_BETWEEN_PINGS,
        Parameter.TIME_BETWEEN_BURST_SEQUENCES,
        Parameter.NUMBER_PINGS,
        Parameter.AVG_INTERVAL,
        Parameter.USER_NUMBER_BEAMS,
        Parameter.TIMING_CONTROL_REGISTER,
        Parameter.POWER_CONTROL_REGISTER,
        Parameter.A1_1_SPARE,
        Parameter.B0_1_SPARE,
        Parameter.B1_1_SPARE,
        Parameter.COMPASS_UPDATE_RATE,
        Parameter.COORDINATE_SYSTEM,
        Parameter.NUMBER_BINS,
        Parameter.BIN_LENGTH,
        Parameter.MEASUREMENT_INTERVAL,
        Parameter.DEPLOYMENT_NAME,
        Parameter.WRAP_MODE,
        Parameter.CLOCK_DEPLOY,
        Parameter.DIAGNOSTIC_INTERVAL,
        Parameter.MODE,
        Parameter.ADJUSTMENT_SOUND_SPEED,
        Parameter.NUMBER_SAMPLES_DIAGNOSTIC,
        Parameter.NUMBER_BEAMS_CELL_DIAGNOSTIC,
        Parameter.NUMBER_PINGS_DIAGNOSTIC,
        Parameter.MODE_TEST,
        Parameter.ANALOG_INPUT_ADDR,
        Parameter.SW_VERSION,
        Parameter.USER_1_SPARE,
        Parameter.VELOCITY_ADJ_TABLE,
        Parameter.COMMENTS,
        Parameter.WAVE_MEASUREMENT_MODE,
        Parameter.DYN_PERCENTAGE_POSITION,
        Parameter.WAVE_TRANSMIT_PULSE,
        Parameter.WAVE_BLANKING_DISTANCE,
        Parameter.WAVE_CELL_SIZE,
        Parameter.NUMBER_DIAG_SAMPLES,
        Parameter.A1_2_SPARE,
        Parameter.B0_2_SPARE,
        Parameter.NUMBER_SAMPLES_PER_BURST,
        Parameter.USER_2_SPARE,
        Parameter.ANALOG_OUTPUT_SCALE,
        Parameter.CORRELATION_THRESHOLD,
        Parameter.USER_3_SPARE,
        Parameter.TRANSMIT_PULSE_LENGTH_SECOND_LAG,
        Parameter.USER_4_SPARE,
        Parameter.QUAL_CONSTANTS]

    def __init__(self, prompts, newline, driver_event):
        """
        Protocol constructor.
        @param prompts A BaseEnum class containing instrument prompts.
        @param newline The newline.
        @param driver_event Driver process event callback.
        """
        # Construct protocol superclass.
        CommandResponseInstrumentProtocol.__init__(self, prompts, newline, driver_event)

        # Build protocol state machine.
        self._protocol_fsm = InstrumentFSM(ProtocolState,
                                           ProtocolEvent,
                                           ProtocolEvent.ENTER,
                                           ProtocolEvent.EXIT)

        # Add event handlers for protocol state machine.
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.ENTER, self._handler_unknown_enter)
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.DISCOVER, self._handler_unknown_discover)
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.EXIT, self._handler_unknown_exit)

        #TODO-RAISE TIMEOUT EXCEPTIONS!!!!
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.ENTER, self._handler_command_enter)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.EXIT, self._handler_command_exit)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.ACQUIRE_SAMPLE, self._handler_command_acquire_sample)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_AUTOSAMPLE, self._handler_command_start_autosample)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.SET, self._handler_command_set)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.GET, self._handler_get)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.ACQUIRE_STATUS, self._handler_command_acquire_status)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_DIRECT, self._handler_command_start_direct)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.SET_CONFIGURATION, self._handler_command_set_configuration)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.READ_CLOCK, self._handler_command_read_clock)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.READ_MODE, self._handler_command_read_mode)
        #self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.POWER_DOWN, self._handler_command_power_down)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.READ_BATTERY_VOLTAGE, self._handler_command_read_battery_voltage)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.READ_ID, self._handler_command_read_id)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.GET_HW_CONFIGURATION, self._handler_command_get_hw_config)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.GET_HEAD_CONFIGURATION, self._handler_command_get_head_config)
        # self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.GET_USER_CONFIGURATION, self._handler_command_get_user_config)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.CLOCK_SYNC, self._handler_command_clock_sync)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.SCHEDULED_CLOCK_SYNC, self._handler_command_clock_sync)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.SCHEDULED_ACQUIRE_STATUS, self._handler_command_acquire_status)

        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.ENTER, self._handler_autosample_enter)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.EXIT, self._handler_autosample_exit)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.STOP_AUTOSAMPLE, self._handler_autosample_stop_autosample)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.SCHEDULED_CLOCK_SYNC, self._handler_autosample_clock_sync)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.SCHEDULED_ACQUIRE_STATUS, self._handler_command_acquire_status)

        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.ENTER, self._handler_direct_access_enter)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.STOP_DIRECT, self._handler_direct_access_stop_direct)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.EXECUTE_DIRECT, self._handler_direct_access_execute_direct)

        # State state machine in UNKNOWN state.
        self._protocol_fsm.start(ProtocolState.UNKNOWN)

        # Add build handlers for device commands.
        self._add_build_handler(InstrumentCmds.CONFIGURE_INSTRUMENT, self._build_set_configuration_command)
        self._add_build_handler(InstrumentCmds.SET_REAL_TIME_CLOCK, self._build_set_real_time_clock_command)

        # Add response handlers for device commands.
        #TODO - RAISE EXCEPTION IF RECEIVED Z_NACK
        self._add_response_handler(InstrumentCmds.READ_REAL_TIME_CLOCK, self._parse_read_clock_response)
        #self._add_response_handler(InstrumentCmds.CMD_WHAT_MODE, self._parse_what_mode_response)
        self._add_response_handler(InstrumentCmds.READ_BATTERY_VOLTAGE, self._parse_read_battery_voltage_response)
        #self._add_response_handler(InstrumentCmds.READ_ID, self._parse_read_id)
        self._add_response_handler(InstrumentCmds.READ_HW_CONFIGURATION, self._parse_read_hw_config)
        self._add_response_handler(InstrumentCmds.READ_HEAD_CONFIGURATION, self._parse_read_head_config)
        self._add_response_handler(InstrumentCmds.READ_USER_CONFIGURATION, self._parse_read_user_config)
        #self._add_response_handler(InstrumentCmds.SAMPLE_AVG_TIME, self._parse_sample_average_interval)
        #self._add_response_handler(InstrumentCmds.SAMPLE_INTERVAL_TIME, self._parse_sample_measurement_interval)

        # Construct the parameter dictionary containing device parameters,
        # current parameter values, and set formatting functions.
        self._build_param_dict()
        self._build_cmd_dict()
        self._build_driver_dict()

    @staticmethod
    def checksum(s):

        size = len(s) / 2
        words = struct.unpack('<%dH' % size, s)
        return struct.pack('<H', (sum(words) + 0xb58c) & 0xffff)

    @staticmethod
    def chunker_sieve_function(raw_data, add_structs=[]):
        """
        The method that detects data sample structures from instrument
        @param structs Additional structures to include in the structure search.
        Should be in the format [[structure_sync_bytes, structure_len]*]
        """
        return_list = []
        structs = add_structs + NORTEK_COMMON_SAMPLE_STRUCTS

        for structure_sync, structure_len in structs:

            index = 0
            start = 0

            #while there are still matches....
            while start != -1:
                start = raw_data.find(structure_sync, index)
                # found a sync pattern
                if start != -1:
                    log.debug("FOUND STRUCT = %r, LENGTH = %s", structure_sync, structure_len)
                    # only check the CRC if all of the structure has arrived
                    if start+structure_len <= len(raw_data):
                        log.debug("start index = %s, end_index = %s", start, start+structure_len)
                        calculated_checksum = NortekProtocolParameterDict.calculate_checksum(raw_data[start:start+structure_len-2], structure_len)
                        sent_checksum = NortekProtocolParameterDict.convert_word_to_int(raw_data[start+structure_len-2:start+structure_len])
                        log.debug('chunker_sieve_function: calculated checksum = %r vs sent_checksum = %s', hex(calculated_checksum), hex(sent_checksum))

                        if sent_checksum == calculated_checksum:

                            return_list.append((start, start+structure_len))
                            #slice raw data off
                            log.debug("chunker_sieve_function: found %r", raw_data[start:start+structure_len])
                            #TODO - RAISE ERROR IF CHECKSUM IS NOT GOOD
                    index = start+structure_len

        # by this point, all the particles with headers have been parsed from the raw data
        # what's left can be battery voltage and/or identification string
        if len(NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS):
            for structure_sync, structure_len in NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS:
                start = raw_data.find(structure_sync)
                if start != -1:    # found a "sync" pattern
                    return_list.append((start, start+len(structure_sync)))
                    log.debug("chunker_sieve_function: found %r", raw_data[start:start+len(structure_sync)].encode('hex'))
                    NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS.remove([structure_sync, structure_len])

        return return_list


    # def _got_chunk_child(self, structure, timestamp):
    #     """
    #     The base class got_data has gotten a structure from the chunker.  Pass it to extract_sample
    #     with the appropriate particle objects and REGEXes.
    #     """
    #
    #     self._extract_sample(NortekUserConfigDataParticle, USER_CONFIG_DATA_REGEX, structure, timestamp)
    #     self._extract_sample(NortekHardwareConfigDataParticle, HARDWARE_CONFIG_DATA_REGEX, structure, timestamp)
    #     self._extract_sample(NortekHeadConfigDataParticle, HEAD_CONFIG_DATA_REGEX, structure, timestamp)
    #
    #     self._extract_sample(NortekEngClockDataParticle, CLOCK_DATA_REGEX, structure, timestamp)
    #     self._extract_sample(NortekEngIdDataParticle, ID_DATA_REGEX, structure, timestamp)
    #
    #     # Note: This appears to be the same size as average interval & measurement interval
    #     # need to copy over the exact regex to match
    #     self._extract_sample(NortekEngBatteryDataParticle, BATTERY_DATA_REGEX, structure, timestamp)




    ########################################################################
    # overridden superclass methods
    ########################################################################

    def _filter_capabilities(self, events):
        """
        Filters capabilities
        """
        events_out = [x for x in events if Capability.has(x)]
        return events_out

    def set_init_params(self, config):
        """
        over-ridden to handle binary block configuration
        Set the initialization parameters to the given values in the protocol
        parameter dictionary.
        @param config A driver configuration dict that should contain an
        enclosed dict with key DriverConfigKey.PARAMETERS. This should include
        either param_name/value pairs or
           {DriverParameter.ALL: base64-encoded string of raw values as the
           instrument would return them from a get config}. If the desired value
           is false, nothing will happen.
        @raise InstrumentParameterException If the config cannot be set
        """
        log.debug("set_init_params: param_config=%s", config)
        if not isinstance(config, dict):
            raise InstrumentParameterException("Invalid init config format")

        param_config = config.get(DriverConfigKey.PARAMETERS)

        if not param_config:
            return

        if DriverParameter.ALL in param_config:
            binary_config = base64.b64decode(param_config[DriverParameter.ALL])
            # make the configuration string look like it came from instrument to get all the methods to be happy
            binary_config += InstrumentPrompts.Z_ACK
            log.debug("binary_config len=%d, binary_config=%s",
                      len(binary_config), binary_config.encode('hex'))

            if len(binary_config) == USER_CONFIG_LEN+2:
                if self._check_configuration(binary_config, USER_CONFIG_SYNC_BYTES, USER_CONFIG_LEN):
                    self._param_dict.update(binary_config)
                else:
                    raise InstrumentParameterException("bad configuration")
            else:
                raise InstrumentParameterException("configuration not the correct length")
        else:
            for name in param_config.keys():
                self._param_dict.set_init_value(name, param_config[name])

    def _set_params(self, *args, **kwargs):
        """
        Issue commands to the instrument to set various parameters
        Also called when setting parameters during startup and direct access

        Issue commands to the instrument to set various parameters.  If
        startup is set to true that means we are setting startup values
        and immutable parameters can be set.  Otherwise only READ_WRITE
        parameters can be set.

        must be overloaded in derived classes

        @param params dictionary containing parameter name and value
        @param startup bool True is we are initializing, False otherwise
        @raise NotImplementedException
        """
        log.debug("%% IN _set_params")

        # Retrieve required parameter from args.
        # Raise exception if no parameter provided, or not a dict.
        params_to_set = None
        try:
            params_to_set = args[0]
            self._verify_not_readonly(*args, **kwargs)

            if not isinstance(params_to_set, dict):
                raise InstrumentParameterException('Set parameters not a dict.')

            parameters = copy.copy(self._param_dict)    # get copy of parameters to modify

            # For each key, value in the params_to_set list set the value in parameters copy.
            try:
                new_value = False
                for (name, value) in params_to_set.iteritems():
                    log.debug('_set_params: setting %s to %s', name, value)
                    if parameters.set_from_value(name, value):
                        log.debug('_set_params: a value was updated: %s', value)
                        new_value = True
            except Exception as ex:
                raise InstrumentParameterException('Unable to set parameter %s to %s: %s' % (name, value, ex))

            output = self._create_set_output(parameters)
            log.debug("Sending to instrument = %r", output)

            self._promptbuf = ''
            self._linebuf = ''

            log.debug('_set_params: writing instrument configuration to instrument')
            self._connection.send(InstrumentCmds.CONFIGURE_INSTRUMENT)
            self._connection.send(output)

            # Clear the prompt buffer.
            #TODO
            #self._get_response(timeout=TIMEOUT, expected_prompt=InstrumentPrompts.Z_ACK)
            self._get_response(timeout=TIMEOUT, expected_prompt=InstrumentPrompts.Z_NACK)
            ret_val = self._update_params()

            if new_value:
                self._driver_event(DriverAsyncEvent.CONFIG_CHANGE)
            elif ret_val is True:
                self._driver_event(DriverAsyncEvent.CONFIG_CHANGE)
                log.debug('_handler_command_set: sending config change event!')

        except IndexError:
            raise InstrumentParameterException('Set params requires a parameter dict.')
        except InstrumentParameterException:
            log.debug("Attempt to set read only parameter(s) (%s)", params_to_set)

    def _get_response(self, timeout=TIMEOUT, expected_prompt=None):
        """
        Get a response from the instrument
        @param timeout The timeout in seconds
        @param expected_prompt Only consider the specific expected prompt as
        presented by this string
        @throw InstrumentProtocolExecption on timeout
        """
        # Grab time for timeout and wait for prompt.
        starttime = time.time()

        if expected_prompt == None:
            prompt_list = self._prompts.list()
        else:
            assert isinstance(expected_prompt, str)
            prompt_list = [expected_prompt]
        while True:
            for item in prompt_list:
                if item in self._promptbuf:
                    return (item, self._linebuf)
                else:
                    time.sleep(.1)
            if time.time() > starttime + timeout:
                raise InstrumentTimeoutException()

    def _do_cmd_resp(self, cmd, *args, **kwargs):
        #TODO - THIS SEEMS TO BE REDUNDANT TO THE BASE CLASS METHOD
        """
        Perform a command-response on the device.
        @param cmd The command to execute.
        @param args positional arguments to pass to the build handler.
        @param timeout=timeout optional command timeout.
        @retval resp_result The (possibly parsed) response result.
        @raises InstrumentTimeoutException if the response did not occur in time.
        @raises InstrumentProtocolException if command could not be built or if response
        was not recognized.
        """

        # Get timeout and initialize response.
        timeout = kwargs.get('timeout', 30)
        expected_prompt = kwargs.get('expected_prompt', InstrumentPrompts.Z_ACK)

        # Clear line and prompt buffers for result.
        self._linebuf = ''
        self._promptbuf = ''

        # Get the build handler.
        build_handler = self._build_handlers.get(cmd, None)
        if build_handler:
            cmd_line = build_handler(cmd, *args, **kwargs)
        else:
            cmd_line = cmd

        # Send command.
        log.debug('_do_cmd_resp: %s(%s), timeout=%s, expected_prompt=%s (%s),',
                  repr(cmd_line), repr(cmd_line.encode("hex")), timeout, expected_prompt, expected_prompt.encode("hex"))
        self._connection.send(cmd_line)

        # Wait for the prompt, prepare result and return, timeout exception
        (prompt, result) = self._get_response(timeout, expected_prompt=expected_prompt)

        resp_handler = self._response_handlers.get((self.get_current_state(), cmd), None) or \
            self._response_handlers.get(cmd, None)
        resp_result = None
        if resp_handler:
            resp_result = resp_handler(result, prompt)

        return resp_result

    ########################################################################
    # Unknown handlers.
    ########################################################################

    def _handler_unknown_enter(self, *args, **kwargs):
        """
        Enter unknown state.
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        log.debug("%%% IN _handler_unknown_enter")
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

    def _handler_unknown_discover(self, *args, **kwargs):
        """
        Discover current state of instrument; can be COMMAND or AUTOSAMPLE.
        @retval (next_state, result)
        """
        log.debug("%%% IN _handler_unknown_discover")

        next_state = None
        result = None

        #TODO
        #send command twice to interrupt the instrument
        self._connection.send(InstrumentCmds.SOFT_BREAK_SECOND_HALF)
        self._do_cmd_resp(InstrumentCmds.SOFT_BREAK_SECOND_HALF)

        for item in self._prompts.list():
            if item in self._promptbuf:
                log.debug('_handler_unknown_discover got prompt: %s' % repr(item))
                if item == InstrumentPrompts.COMMAND_MODE:
                    next_state = ProtocolState.COMMAND
                    result = ResourceAgentState.IDLE
                elif item == InstrumentPrompts.CONFIRMATION:
                    next_state = ProtocolState.AUTOSAMPLE
                    result = ResourceAgentState.STREAMING

        log.debug('_handler_unknown_discover: state=%s', next_state)

        return next_state, result

    def _handler_unknown_exit(self, *args, **kwargs):
        """
        Exiting Unknown state
        """
        log.debug("%%% IN _handler_unknown_exit")
        pass

    ########################################################################
    # Command handlers.
    ########################################################################

    def _handler_command_enter(self, *args, **kwargs):
        """
        Enter command state.
        @throws InstrumentTimeoutException if the device cannot be woken.
        @throws InstrumentProtocolException if the update commands and not recognized.
        """
        log.debug('%% IN _handler_command_enter')
        # Command device to update parameters and send a config change event.

        #TODO
        self._update_params()
        self._init_params()

        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

        log.debug("Configuring the scheduler to sync clock %s", self._param_dict.get(EngineeringParameter.CLOCK_SYNC_INTERVAL))
        if self._param_dict.get(EngineeringParameter.CLOCK_SYNC_INTERVAL) != '00:00:00':
            self.start_scheduled_job(EngineeringParameter.CLOCK_SYNC_INTERVAL, ScheduledJob.CLOCK_SYNC, ProtocolEvent.CLOCK_SYNC)

        log.debug("Configuring the scheduler to acquire status %s", self._param_dict.get(EngineeringParameter.ACQUIRE_STATUS_INTERVAL))
        if self._param_dict.get(EngineeringParameter.ACQUIRE_STATUS_INTERVAL) != '00:00:00':
            self.start_scheduled_job(EngineeringParameter.ACQUIRE_STATUS_INTERVAL, ScheduledJob.ACQUIRE_STATUS, ProtocolEvent.ACQUIRE_STATUS)

    def _handler_command_exit(self, *args, **kwargs):
        """
        Exit command state.
        """
        log.debug('%% IN _handler_command_exit')

        self.stop_scheduled_job(ScheduledJob.ACQUIRE_STATUS)
        self.stop_scheduled_job(ScheduledJob.CLOCK_SYNC)
        pass

    def _handler_command_acquire_sample(self, *args, **kwargs):
        """
        Command the instrument to acquire sample data. Instrument will enter Power Down mode when finished
        """
        log.debug('%% IN _handler_command_acquire_sample')

        result = self._do_cmd_resp(InstrumentCmds.ACQUIRE_DATA)

        return None, (None, result)

    def _handler_command_acquire_status(self, *args, **kwargs):
        log.debug('%% IN _handler_command_acquire_status')

        #result = self._do_cmd_resp(InstrumentCmds.READ_BATTERY_VOLTAGE)
        #self._handler_command_read_battery_voltage()

        #RC
        #self._handler_command_read_clock()
        # self._do_cmd_resp(InstrumentCmds.READ_REAL_TIME_CLOCK)

        #GP
        self._handler_command_get_hw_config()
        # self._do_cmd_resp(InstrumentCmds.READ_HW_CONFIGURATION)

        #GH
        self._handler_command_get_head_config()
        # self._do_cmd_resp(InstrumentCmds.READ_HEAD_CONFIGURATION)

        #GC
        self._handler_command_get_user_config()
        # self._do_cmd_resp(InstrumentCmds.READ_USER_CONFIGURATION)

        #todo
        #II - No data particle for this data, don't think this is needed
        # self._handler_command_read_mode()
        #self._do_cmd_resp(InstrumentCmds.READ_ID)


        return None, (None, None)

    def _handler_command_set(self, *args, **kwargs):
        """
        Perform a set command.
        @param args[0] parameter : value dict.
        @retval (next_state, result) tuple, (None, None).
        @throws InstrumentParameterException if missing set parameters, if set parameters not ALL and
        not a dict, or if parameter can't be properly formatted.
        @throws InstrumentTimeoutException if device cannot be woken for set command.
        @throws InstrumentProtocolException if set command could not be built or misunderstood.
        """
        log.debug('%% IN _handler_command_set')
        try:
            params = args[0]
            log.debug('Params = %s', params)
        except IndexError:
            raise InstrumentParameterException('_handler_command_set Set command requires a parameter dict.')

        try:
            startup = args[1]
        except IndexError:
            startup = False
            log.debug("NO STARTUP VALUE")
            pass

        if not isinstance(params, dict):
            raise InstrumentParameterException('Set parameters not a dict.')

        # For each key, val in the dict, issue set command to device.
        # Raise if the command not understood.
        else:
            self._set_params(params, startup)

        return None, None

    def _handler_command_start_autosample(self, *args, **kwargs):
        """
        Switch into autosample mode, syncing the clock first
        @retval (next_state, result) tuple, (AUTOSAMPLE, None) if successful.
        @throws InstrumentTimeoutException if device cannot be woken for command.
        @throws InstrumentProtocolException if command could not be built or misunderstood.
        """
        log.debug('%% IN _handler_command_start_autosample')

        next_state = None
        next_agent_state = None

        self._protocol_fsm.on_event(ProtocolEvent.CLOCK_SYNC)

        # Issue start command and switch to autosample if successful.
        # RECORDER
        result = self._do_cmd_resp(InstrumentCmds.START_MEASUREMENT_WITHOUT_RECORDER, *args, **kwargs)

        next_state = ProtocolState.AUTOSAMPLE
        next_agent_state = ResourceAgentState.STREAMING

        return next_state, (next_agent_state, result)

    def _handler_command_start_direct(self):
        """
        Start Direct Access
        """
        log.debug('%% IN _handler_start_direct: entering DA mode')

        next_state = None
        result = None

        next_agent_state = ResourceAgentState.DIRECT_ACCESS
        next_state = ProtocolState.DIRECT_ACCESS

        return next_state, (next_agent_state, result)

    def _handler_command_read_clock(self):
        """
        """
        next_state = None
        next_agent_state = None

        # Issue read clock command.
        result = self._do_cmd_resp(InstrumentCmds.READ_REAL_TIME_CLOCK, timeout=TIMEOUT)

        return next_state, (next_agent_state, result)

    def _handler_command_read_battery_voltage(self):
        """
        """

        log.debug('%% IN _handler_command_read_battery_voltage')

        next_state = None
        next_agent_state = None

        # Issue read battery command.
        result = self._do_cmd_resp(InstrumentCmds.READ_BATTERY_VOLTAGE)

        return next_state, (next_agent_state, result)

    def _handler_command_read_id(self):
        """
        """
        next_state = None
        next_agent_state = None

        # Issue read clock command.
        result = self._do_cmd_resp(InstrumentCmds.READ_ID)

        return next_state, (next_agent_state, result)

    def _handler_command_get_hw_config(self):
        """
        """
        next_state = None
        next_agent_state = None
        result = None
        log.debug('%% IN _handler_command_get_hw_config')
        # Issue read hw config command.


        result = self._do_cmd_resp(InstrumentCmds.READ_HW_CONFIGURATION)

        return next_state, (next_agent_state, result)

    def _handler_command_get_head_config(self):
        """
        """
        log.debug('%% IN _handler_command_get_head_config')
        next_state = None
        next_agent_state = None

        # Issue read clock command.
        result = self._do_cmd_resp(InstrumentCmds.READ_HEAD_CONFIGURATION)

        return next_state, (next_agent_state, result)

    def _handler_command_get_user_config(self):
        """
        """
        next_state = None
        next_agent_state = None
        log.debug('%% IN _handler_command_get_user_config')

        # Issue read clock command.
        result = self._do_cmd_resp(InstrumentCmds.READ_USER_CONFIGURATION)

        return next_state, (next_agent_state, result)

    def _clock_sync(self, *args, **kwargs):
        """
        The mechanics of synchronizing a clock
        @throws InstrumentTimeoutException if device cannot be woken for command.
        @throws InstrumentProtocolException if command could not be built or misunderstood.
        """
        log.debug('%% IN _clock_sync')
        str_time = get_timestamp_delayed("%M %S %d %H %y %m")
        byte_time = ''
        for v in str_time.split():
            byte_time += chr(int('0x'+v, base=16))
        values = str_time.split()
        log.info("_clock_sync: time set to %s:m %s:s %s:d %s:h %s:y %s:M (%s)",
                 values[0], values[1], values[2], values[3], values[4], values[5],
                 byte_time.encode('hex'))
        result = self._do_cmd_resp(InstrumentCmds.SET_REAL_TIME_CLOCK, byte_time, **kwargs)

        return result

    def _handler_command_clock_sync(self, *args, **kwargs):
        """
        sync clock close to a second edge
        @retval (next_state, result) tuple, (None, None) if successful.
        @throws InstrumentTimeoutException if device cannot be woken for command.
        @throws InstrumentProtocolException if command could not be built or misunderstood.
        """
        log.debug('%% IN _handler_command_clock_sync')

        next_state = None
        next_agent_state = None
        result = None
        self._clock_sync()
        return next_state, (next_agent_state, result)


    ########################################################################
    # Autosample handlers.
    ########################################################################
    def _handler_autosample_clock_sync(self, *args, **kwargs):
        """
        While in autosample, sync a clock close to a second edge
        @retval (next_state, result) tuple, (None, None) if successful.
        @throws InstrumentTimeoutException if device cannot be woken for command.
        @throws InstrumentProtocolException if command could not be built or misunderstood.
        """
        log.debug('%% IN _handler_autosample_clock_sync')

        next_state = None
        next_agent_state = None
        result = None
        try:
            self._protocol_fsm._on_event(InstrumentCmds.STOP_AUTOSAMPLE)
            next_state = ProtocolState.COMMAND
            next_agent_state = ResourceAgentState.COMMAND
            self._clock_sync()
            self._protocol_fsm._on_event(InstrumentCmds.START_AUTOSAMPLE)
            next_state = ProtocolState.AUTOSAMPLE
            next_agent_state = ResourceAgentState.STREAMING
        finally:
            return (next_state, (next_agent_state, result))

        return next_state, (next_agent_state, result)

    def _handler_autosample_enter(self, *args, **kwargs):
        """
        Enter autosample state.
        """
        log.debug('%% IN _handler_autosample_enter')

        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

        log.debug("Configuring the scheduler to sync clock %s", self._param_dict.get(EngineeringParameter.CLOCK_SYNC_INTERVAL))
        if self._param_dict.get(EngineeringParameter.CLOCK_SYNC_INTERVAL) != '00:00:00':
            self.start_scheduled_job(EngineeringParameter.CLOCK_SYNC_INTERVAL, ScheduledJob.CLOCK_SYNC, ProtocolEvent.CLOCK_SYNC)

        log.debug("Configuring the scheduler to acquire status %s", self._param_dict.get(EngineeringParameter.ACQUIRE_STATUS_INTERVAL))
        if self._param_dict.get(EngineeringParameter.ACQUIRE_STATUS_INTERVAL) != '00:00:00':
            self.start_scheduled_job(EngineeringParameter.ACQUIRE_STATUS_INTERVAL, ScheduledJob.ACQUIRE_STATUS, ProtocolEvent.ACQUIRE_STATUS)


    def _handler_autosample_exit(self, *args, **kwargs):
        """
        Exit autosample state.
        """
        log.debug("%%% IN _handler_autosample_exit")

        self.stop_scheduled_job(ScheduledJob.ACQUIRE_STATUS)
        self.stop_scheduled_job(ScheduledJob.CLOCK_SYNC)
        pass

    def _helper_measurement_to_command_mode(self, *args, **kwargs):

        # send soft break
        self._connection.send(InstrumentCmds.SOFT_BREAK_FIRST_HALF)
        time.sleep(.1)
        self._do_cmd_resp(InstrumentCmds.SOFT_BREAK_SECOND_HALF,
                          expected_prompt=InstrumentPrompts.CONFIRMATION, *args, **kwargs)

        # Issue the confirmation command.
        self._do_cmd_resp(InstrumentCmds.CONFIRMATION, *args, **kwargs)

        return None

    def stop_scheduled_job(self, schedule_job):
        """
        Remove the scheduled job
        """
        log.debug("Attempting to remove the scheduler")
        if self._scheduler is not None:
            try:
                self._remove_scheduler(schedule_job)
                log.debug("successfully removed scheduler")
            except KeyError:
                log.debug("_remove_scheduler could not find %s", schedule_job)

    def start_scheduled_job(self, param, schedule_job, protocol_event):
        """
        Add a scheduled job
        """
        interval = self._param_dict.get(param).split(':')
        hours = interval[0]
        minutes = interval[1]
        seconds = interval[2]
        log.debug("Setting scheduled interval to: %s %s %s", hours, minutes, seconds)

        config = {DriverConfigKey.SCHEDULER: {
            schedule_job: {
                DriverSchedulerConfigKey.TRIGGER: {
                    DriverSchedulerConfigKey.TRIGGER_TYPE: TriggerType.INTERVAL,
                    DriverSchedulerConfigKey.HOURS: int(hours),
                    DriverSchedulerConfigKey.MINUTES: int(minutes),
                    DriverSchedulerConfigKey.SECONDS: int(seconds)
                }
            }
        }
        }
        self.set_init_params(config)

        log.debug("Adding job %s", schedule_job)
        try:
            self._add_scheduler_event(schedule_job, protocol_event)
        except KeyError:
            log.debug("duplicate scheduler exists for '%s'", schedule_job)

    def _handler_autosample_stop_autosample(self, *args, **kwargs):
        """
        Stop autosample and switch back to command mode.
        @retval (next_state, result) tuple, (SBE37ProtocolState.COMMAND,
        None) if successful.
        @throws InstrumentTimeoutException if device cannot be woken for command.
        @throws InstrumentProtocolException if command misunderstood or
        incorrect prompt received.
        """
        log.debug('%% IN _handler_autosample_stop_autosample')

        next_state = None
        result = None

        self._helper_measurement_to_command_mode(*args, **kwargs)

        next_state = ProtocolState.COMMAND
        next_agent_state = ResourceAgentState.COMMAND

        return next_state, (next_agent_state, result)

    ########################################################################
    # Direct access handlers.
    ########################################################################

    def _handler_direct_access_enter(self, *args, **kwargs):
        """
        Enter direct access state.
        """
        log.debug('%% IN _handler_direct_access_enter')
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        self._sent_cmds = []

    def _handler_direct_access_exit(self, *args, **kwargs):
        """
        Exit direct access state.
        """
        log.debug('%% IN _handler_direct_access_exit')
        pass

    def _handler_direct_access_execute_direct(self, data):
        """
        Execute Direct Access command(s)
        """
        log.debug('%% IN _handler_direct_access_execute_direct')
        next_state = None
        result = None

        self._do_cmd_direct(data)

        # add sent command to list for 'echo' filtering in callback
        self._sent_cmds.append(data)

        return next_state, result

    def _handler_direct_access_stop_direct(self):
        """
        Stop Direct Access, and put the driver into a healthy state by reverting itself back to the previous
        state before starting Direct Access.
        @throw InstrumentProtocolException on invalid command
        """
        log.debug("%% IN _handler_direct_access_stop_direct")

        next_state = None
        result = None

        #TODO
        #discover the state to go to next
        next_state, next_agent_state = self._handler_unknown_discover()
        if next_state == DriverProtocolState.COMMAND:
            next_agent_state = ResourceAgentState.COMMAND

        # if next_state == DriverProtocolState.AUTOSAMPLE:
        #     #go into command mode
        #     self._do_cmd_no_resp(InstrumentCommand.INTERRUPT_INSTRUMENT)
        #
        # da_params = self.get_direct_access_params()
        # log.debug("DA params to reset: %s", da_params)
        # for param in da_params:
        #
        #     log.debug('Trying to reset param %s', param)
        #
        #     old_val = self._param_dict.get(param)
        #     new_val = self._param_dict.get_default_value(param)
        #
        #     log.debug('Comparing %s == %s', old_val, new_val)
        #
        #     #if setting the mvs interval or clock sync interval, do not send a command
        #     if param == Parameter.RUN_WIPER_INTERVAL or param == Parameter.RUN_CLOCK_SYNC_INTERVAL or param == Parameter.RUN_ACQUIRE_STATUS_INTERVAL:
        #         self._param_dict.set_value(param, new_val)
        #     #else if setting the clock or date, run clock sync command
        #     elif param == Parameter.TIME or param == Parameter.DATE:
        #         self._sync_clock()
        #     #else perform regular command
        #     else:
        #         #if old_val != new_val:
        #         self._param_dict.set_value(param, new_val)
        #         self._do_cmd_resp(InstrumentCommand.SET, param, new_val, response_regex=MNU_REGEX_MATCHER)
        #
        # if next_state == DriverProtocolState.AUTOSAMPLE:
        #     #go into autosample mode
        #     self._do_cmd_no_resp(InstrumentCommand.RUN_SETTINGS)

        log.debug("Next_state = %s, Next_agent_state = %s", next_state, next_agent_state)
        return next_state, (next_agent_state, None)


    ########################################################################
    # Common handlers.
    ########################################################################
    def _handler_get(self, *args, **kwargs):
        """
        Get device parameters from the parameter dict.
        @param args[0] list of parameters to retrieve, or DriverParameter.ALL.
        @throws InstrumentParameterException if missing or invalid parameter.
        """
        next_state = None
        result = None

        # Retrieve the required parameter, raise if not present.
        try:
            params = args[0]

        except IndexError:
            raise InstrumentParameterException('Get command requires a parameter list or tuple.')
        # If all params requested, retrieve config.
        if (params == DriverParameter.ALL) or (params == [DriverParameter.ALL]):
            result = self._param_dict.get_config()

        # If not all params, confirm a list or tuple of params to retrieve.
        # Raise if not a list or tuple.
        # Retrieve each key in the list, raise if any are invalid.
        else:
            if not isinstance(params, (list, tuple)):
                raise InstrumentParameterException('Get argument not a list or tuple.')
            result = {}
            for key in params:
                try:
                    val = self._param_dict.get(key)
                    result[key] = val

                except KeyError:
                    raise InstrumentParameterException(('%s is not a valid parameter.' % key))

        return next_state, result


    def _build_driver_dict(self):
        """
        Build a driver dictionary structure, load the strings for the metadata
        from a file if present.
        """
        log.debug("%%% IN _build_driver_dict")
        self._driver_dict = DriverDict()
        self._driver_dict.add(DriverDictKey.VENDOR_SW_COMPATIBLE, True)

    def _build_cmd_dict(self):
        """
        Build a command dictionary structure, load the strings for the metadata
        from a file if present.
        """
        log.debug("%%% IN _build_cmd_dict")
        self._cmd_dict = ProtocolCommandDict()
        self._cmd_dict.add(Capability.SET, display_name='set')
        self._cmd_dict.add(Capability.GET, display_name='get')
        self._cmd_dict.add(Capability.ACQUIRE_SAMPLE, display_name='acquire sample')
        self._cmd_dict.add(Capability.START_AUTOSAMPLE, display_name='start autosample')
        self._cmd_dict.add(Capability.STOP_AUTOSAMPLE, display_name='stop autosample')
        self._cmd_dict.add(Capability.CLOCK_SYNC, display_name='clock sync')
        self._cmd_dict.add(Capability.START_DIRECT, display_name='start direct access')
        self._cmd_dict.add(Capability.STOP_DIRECT, display_name='stop direct access')
        self._cmd_dict.add(Capability.ACQUIRE_STATUS, display_name='acquire status')
        #self._cmd_dict.add(Capability.SET_CONFIGURATION, display_name='')
        # self._cmd_dict.add(Capability.READ_CLOCK, display_name='')
        # self._cmd_dict.add(Capability.READ_MODE, display_name='')
        # self._cmd_dict.add(Capability.POWER_DOWN, display_name='')
        # self._cmd_dict.add(Capability.READ_BATTERY_VOLTAGE, display_name='')
        # self._cmd_dict.add(Capability.READ_ID, display_name='')
        # self._cmd_dict.add(Capability.GET_HW_CONFIGURATION, display_name='')
        # self._cmd_dict.add(Capability.GET_HEAD_CONFIGURATION, display_name='')
        # self._cmd_dict.add(Capability.GET_USER_CONFIGURATION, display_name='')


    def _build_param_dict(self):
        """
        Populate the parameter dictionary with parameters.
        For each parameter key, add match string, match lambda function,
        and value formatting function for set commands.
        """
        log.debug("%%% IN _build_param_dict")

        self._param_dict = NortekProtocolParameterDict()

        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.TRANSMIT_PULSE_LENGTH,
                                r'^.{%s}(.{2}).*' % str(4),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_WRITE,
                                display_name="transmit pulse length",
                                default_value=2,
                                init_value=2,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.BLANKING_DISTANCE,
                                r'^.{%s}(.{2}).*' % str(6),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_WRITE,
                                display_name="blanking distance",
                                default_value=16,
                                init_value=16,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.RECEIVE_LENGTH,
                                r'^.{%s}(.{2}).*' % str(8),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_WRITE,
                                display_name="receive length",
                                default_value=7,
                                init_value=7,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.TIME_BETWEEN_PINGS,
                                r'^.{%s}(.{2}).*' % str(10),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_WRITE,
                                display_name="time between pings",
                                default_value=None,
                                init_value=44,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.TIME_BETWEEN_BURST_SEQUENCES,
                                r'^.{%s}(.{2}).*' % str(12),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.IMMUTABLE,
                                display_name="time between burst sequences",
                                default_value=0,
                                init_value=0,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.NUMBER_PINGS,
                                r'^.{%s}(.{2}).*' % str(14),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.IMMUTABLE,
                                display_name="number pings",
                                default_value=0,
                                init_value=0,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.AVG_INTERVAL,
                                r'^.{%s}(.{2}).*' % str(16),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_WRITE,
                                display_name="avg interval",
                                default_value=32,
                                init_value=32,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.USER_NUMBER_BEAMS,
                                r'^.{%s}(.{2}).*' % str(18),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="user number beams",
                                #TODO IF THIS IS A FIXED VALUE WHY ALLOW TO CHANGE?
                                default_value=3,
                                init_value=3,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.TIMING_CONTROL_REGISTER,
                                r'^.{%s}(.{2}).*' % str(20),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="timing control register"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.POWER_CONTROL_REGISTER,
                                r'^.{%s}(.{2}).*' % str(22),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="power control register"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.A1_1_SPARE,
                                r'^.{%s}(.{2}).*' % str(24),
                                lambda match: match.group(1),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="a1 1 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.B0_1_SPARE,
                                r'^.{%s}(.{2}).*' % str(26),
                                lambda match: match.group(1),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="b0 1 spare",
                                default_value=None))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.B1_1_SPARE,
                                r'^.{%s}(.{2}).*' % str(28),
                                lambda match: match.group(1),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="b1 1 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.COMPASS_UPDATE_RATE,
                                r'^.{%s}(.{2}).*' % str(30),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="compass update rate",
                                default_value=1,
                                init_value=1,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.COORDINATE_SYSTEM,
                                r'^.{%s}(.{2}).*' % str(32),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_WRITE,
                                display_name="coordinate system",
                                default_value=0,
                                init_value=0,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.NUMBER_BINS,
                                r'^.{%s}(.{2}).*' % str(34),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="number bins",
                                default_value=1,
                                init_value=1,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.BIN_LENGTH,
                                r'^.{%s}(.{2}).*' % str(36),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="bin length",
                                default_value=7,
                                init_value=7,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.MEASUREMENT_INTERVAL,
                                r'^.{%s}(.{2}).*' % str(38),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="measurement interval",
                                default_value=3600,
                                init_value=3600,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.DEPLOYMENT_NAME,
                                r'^.{%s}(.{6}).*' % str(40),
                                lambda match: NortekProtocolParameterDict.convert_bytes_to_string(match.group(1)),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="deployment name"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.WRAP_MODE,
                                r'^.{%s}(.{2}).*' % str(46),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="wrap mode"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.CLOCK_DEPLOY,
                                r'^.{%s}(.{6}).*' % str(48),
                                lambda match: NortekProtocolParameterDict.convert_words_to_datetime(match.group(1)),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="clock deploy"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.DIAGNOSTIC_INTERVAL,
                                r'^.{%s}(.{4}).*' % str(54),
                                lambda match: NortekProtocolParameterDict.convert_double_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.double_word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="diagnostic interval",
                                default_value=10800,
                                init_value=10800,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.MODE,
                                r'^.{%s}(.{2}).*' % str(58),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="mode",
                                default_value=48,
                                init_value=48, #0000000000110000
                                startup_param=False,  # True, TODO find correct initial value
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.ADJUSTMENT_SOUND_SPEED,
                                r'^.{%s}(.{2}).*' % str(60),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_WRITE,
                                display_name="adjustment sound speed",
                                default_value=1525,
                                init_value=16657,
                                startup_param=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.NUMBER_SAMPLES_DIAGNOSTIC,
                                r'^.{%s}(.{2}).*' % str(62),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="number samples diagnostic",
                                default_value=1,
                                init_value=1,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.NUMBER_BEAMS_CELL_DIAGNOSTIC,
                                r'^.{%s}(.{2}).*' % str(64),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="number beams cell diagnostic",
                                default_value=1,
                                init_value=1,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.NUMBER_PINGS_DIAGNOSTIC,
                                r'^.{%s}(.{2}).*' % str(66),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="number pings diagnostic",
                                default_value=1,
                                init_value=1,
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.MODE_TEST,
                                r'^.{%s}(.{2}).*' % str(68),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="mode test",
                                default_value=4,
                                init_value=4, #00000000 00000100
                                startup_param=False,  # True, TODO find correct initial value
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.ANALOG_INPUT_ADDR,
                                r'^.{%s}(.{2}).*' % str(70),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="analog input addr"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.SW_VERSION,
                                r'^.{%s}(.{2}).*' % str(72),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="sw version"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.USER_1_SPARE,
                                r'^.{%s}(.{2}).*' % str(74),
                                lambda match: match.group(1),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="user 1 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.VELOCITY_ADJ_TABLE,
                                r'^.{%s}(.{180}).*' % str(76),
                                lambda match: base64.b64encode(match.group(1)),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="velocity adj table",
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.COMMENTS,
                                r'^.{%s}(.{180}).*' % str(256),
                                lambda match: NortekProtocolParameterDict.convert_bytes_to_string(match.group(1)),
                                lambda string : string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name='Aj0ePTk9Uz1uPYg9oj27PdQ97T0GPh4+Nj5OPmU+fT6TPqo+wD7WP'
                                                                 'uw+Aj8XPyw/QT9VP2k/fT+RP6Q/uD/KP90/8D8CQBRAJkA3QElAW'
                                                                 'kBrQHxAjECcQKxAvEDMQNtA6kD5QAhBF0ElQTNBQkFPQV1BakF4'
                                                                 'QYVBkkGeQatBt0HDQc9B20HnQfJB/UEIQhNCHkIoQjNCPUJHQl'
                                                                 'FCW0JkQm5Cd0K',
                                init_value='Aj0ePTk9Uz1uPYg9oj27PdQ97T0GPh4+Nj5OPmU+fT6TPqo+wD7WP'
                                                                 'uw+Aj8XPyw/QT9VP2k/fT+RP6Q/uD/KP90/8D8CQBRAJkA3QElAW'
                                                                 'kBrQHxAjECcQKxAvEDMQNtA6kD5QAhBF0ElQTNBQkFPQV1BakF4'
                                                                 'QYVBkkGeQatBt0HDQc9B20HnQfJB/UEIQhNCHkIoQjNCPUJHQl'
                                                                 'FCW0JkQm5Cd0K',
                                startup_param=True,
                                direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.WAVE_MEASUREMENT_MODE,
                                r'^.{%s}(.{2}).*' % str(436),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="wave measurement mode"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.DYN_PERCENTAGE_POSITION,
                                r'^.{%s}(.{2}).*' % str(438),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="dyn percentage position"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.WAVE_TRANSMIT_PULSE,
                                r'^.{%s}(.{2}).*' % str(440),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="wave transmit pulse"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.WAVE_BLANKING_DISTANCE,
                                r'^.{%s}(.{2}).*' % str(442),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="wave blanking distance"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.WAVE_CELL_SIZE,
                                r'^.{%s}(.{2}).*' % str(444),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="wave cell size"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.NUMBER_DIAG_SAMPLES,
                                r'^.{%s}(.{2}).*' % str(446),
                                lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                NortekProtocolParameterDict.word_to_string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.INT,
                                visibility=ParameterDictVisibility.READ_ONLY,
                                display_name="number diag samples"))     # Does this control diagnostic output?
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.A1_2_SPARE,
                                    r'^.{%s}(.{2}).*' % str(448),
                                   lambda match: match.group(1),
                                    lambda string : string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.STRING,
                                    visibility=ParameterDictVisibility.READ_ONLY,
                                   display_name="a1 2 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.B0_2_SPARE,
                                    r'^.{%s}(.{2}).*' % str(450),
                                   lambda match: match.group(1),
                                    lambda string : string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.STRING,
                                    visibility=ParameterDictVisibility.READ_ONLY,
                                   display_name="b0 2 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.NUMBER_SAMPLES_PER_BURST,
                                    r'^.{%s}(.{2}).*' % str(452),
                                   lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                    NortekProtocolParameterDict.word_to_string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.INT,
                                    visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                    display_name="number samples per burst",
                                    default_value=0,
                                    init_value=0,
                                    startup_param=True,
                                    direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.USER_2_SPARE,          # for Vector this is 'SAMPLE_RATE'
                                    r'^.{%s}(.{2}).*' % str(454),
                                    lambda match: match.group(1),
                                    lambda string : string,
                                    # init_value=,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.STRING,
                                    visibility=ParameterDictVisibility.READ_ONLY,       # This might change based on OIS
                                    display_name="user 2 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.ANALOG_OUTPUT_SCALE,
                                    r'^.{%s}(.{2}).*' % str(456),
                                    lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                    NortekProtocolParameterDict.word_to_string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.INT,
                                    visibility=ParameterDictVisibility.READ_ONLY,
                                    display_name="analog output scale"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.CORRELATION_THRESHOLD,
                                    r'^.{%s}(.{2}).*' % str(458),
                                   lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                    NortekProtocolParameterDict.word_to_string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.INT,
                                   visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                    display_name="correlation threshold",
                                    default_value=0,
                                    init_value=0,
                                    startup_param=True,
                                    direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.USER_3_SPARE,
                                    r'^.{%s}(.{2}).*' % str(460),
                                   lambda match: match.group(1),
                                    lambda string : string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.STRING,
                                    visibility=ParameterDictVisibility.READ_ONLY,
                                   display_name="user 3 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.TRANSMIT_PULSE_LENGTH_SECOND_LAG,
                                    r'^.{%s}(.{2}).*' % str(462),
                                   lambda match: NortekProtocolParameterDict.convert_word_to_int(match.group(1)),
                                    NortekProtocolParameterDict.word_to_string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.INT,
                                   visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                    display_name="transmit pulse length second lag",
                                    default_value=2,
                                    init_value=2,
                                    startup_param=True,
                                    direct_access=True))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.USER_4_SPARE,
                                    r'^.{%s}(.{30}).*' % str(464),
                                   lambda match: match.group(1),
                                   lambda string: string,
                                    regex_flags=re.DOTALL,
                                    type=ParameterDictType.STRING,
                                    visibility=ParameterDictVisibility.READ_ONLY,
                                   display_name="user 4 spare"))
        self._param_dict.add_parameter(
            NortekParameterDictVal(Parameter.QUAL_CONSTANTS,
                                r'^.{%s}(.{16}).*' % str(494),
                                lambda match: base64.b64encode(match.group(1)),
                                lambda string: string,
                                regex_flags=re.DOTALL,
                                type=ParameterDictType.STRING,
                                visibility=ParameterDictVisibility.DIRECT_ACCESS,
                                display_name="qual constants",
                                default_value='Cv/N/4sA5QDuAAsAhP89/w==',
                                init_value='Cv/N/4sA5QDuAAsAhP89/w==',
                                startup_param=True,
                                direct_access=True))

        ############################################################################
        # ENGINEERING PARAMETERS
        ###########################################################################
        self._param_dict.add_parameter(
                                   NortekParameterDictVal(EngineeringParameter.CLOCK_SYNC_INTERVAL,
                                   INTERVAL_TIME_REGEX,
                                   lambda match: match.group(1),
                                   str,
                                   type=ParameterDictType.STRING,
                                   visibility=ParameterDictVisibility.IMMUTABLE,
                                   display_name="clock sync interval",
                                   default_value='00:00:00',
                                   startup_param=True))
        self._param_dict.add_parameter(
                                   NortekParameterDictVal(EngineeringParameter.ACQUIRE_STATUS_INTERVAL,
                                   INTERVAL_TIME_REGEX,
                                   lambda match: match.group(1),
                                   str,
                                   type=ParameterDictType.STRING,
                                   visibility=ParameterDictVisibility.IMMUTABLE,
                                   display_name="acquire status interval",
                                   default_value='00:00:00',
                                   startup_param=True))

        #set the values of the dictionary using set_default
        for param in self._param_dict.get_keys():
            self._param_dict.set_value(param, self._param_dict.get_default_value(param))

        self._param_dict.set_value(Parameter.TIMING_CONTROL_REGISTER, 36)
        self._param_dict.set_value(Parameter.POWER_CONTROL_REGISTER, 0)
        self._param_dict.set_value(Parameter.A1_1_SPARE, "0")
        self._param_dict.set_value(Parameter.B0_1_SPARE, "0")
        self._param_dict.set_value(Parameter.B1_1_SPARE, "0")
        self._param_dict.set_value(Parameter.WRAP_MODE, 0)
        self._param_dict.set_value(Parameter.USER_1_SPARE, "0")
        self._param_dict.set_value(Parameter.DYN_PERCENTAGE_POSITION, 0)
        self._param_dict.set_value(Parameter.WAVE_TRANSMIT_PULSE, 0)
        self._param_dict.set_value(Parameter.WAVE_BLANKING_DISTANCE, 0)
        self._param_dict.set_value(Parameter.WAVE_CELL_SIZE, 0)
        self._param_dict.set_value(Parameter.NUMBER_DIAG_SAMPLES, 0)
        self._param_dict.set_value(Parameter.ANALOG_OUTPUT_SCALE, 0)
        #self._param_dict.set_value(Parameter.VELOCITY_ADJ_TABLE, #'01 02 3d 1e 3d 39 3d 53 3d 6e 3d 88 3d a2 3d bb 3d d4 3d ed 3d 06 3e 1e 3e 36 3e 4e 3e 65 3e 7d 3e 93 3e aa 3e c0 3e d6 3e ec 3e 02 3f 17 3f 2c 3f 41 3f 55 3f 69 3f 7d 3f 91 3f a4 3f b8 3f ca 3f dd 3f f0 3f 02 40 14 40 26 40 37 40 49 40 5a 40 6b 40 7c 40 8c 40 9c 40 ac 40 bc 40 cc 40 db 40 ea 40 f9 40 08 41 17 41 25 41 33 41 42 41 4f 41 5d 41 6a 41 78 41 85 41 92 41 9e 41 ab 41 b7 41 c3 41 cf 41 db 41 e7 41 f2 41 fd 41 08 42 13 42 1e 42 28 42 33 42 3d 42 47 42 51 42 5b 42 64 42 6e 42 77 42 80 42 89 42 91 42 9a 42 a2 42 aa 42 b2 42 ba 42 ')
        self._param_dict.set_value(Parameter.VELOCITY_ADJ_TABLE, 'Aj0ePTk9Uz1uPYg9oj27PdQ97T0GPh4+Nj5OPmU+fT6TPqo+wD7WP'
                                                                 'uw+Aj8XPyw/QT9VP2k/fT+RP6Q/uD/KP90/8D8CQBRAJkA3QElAW'
                                                                 'kBrQHxAjECcQKxAvEDMQNtA6kD5QAhBF0ElQTNBQkFPQV1BakF4'
                                                                 'QYVBkkGeQatBt0HDQc9B20HnQfJB/UEIQhNCHkIoQjNCPUJHQl'
                                                                 'FCW0JkQm5Cd0KAQolCkUKaQqJCqkKyQrpC')
                                   #"0000000000000000000000000000000000000000000000000000")


        self._param_dict.set_value(Parameter.DEPLOYMENT_NAME, "0")
        self._param_dict.set_value(Parameter.CLOCK_DEPLOY, "010214")
        self._param_dict.set_value(Parameter.ANALOG_INPUT_ADDR, 0)
        self._param_dict.set_value(Parameter.SW_VERSION, 0)
        self._param_dict.set_value(Parameter.WAVE_MEASUREMENT_MODE, 0)
        self._param_dict.set_value(Parameter.A1_2_SPARE, "0")
        self._param_dict.set_value(Parameter.B0_2_SPARE, "0")
        self._param_dict.set_value(Parameter.USER_2_SPARE, "0")
        self._param_dict.set_value(Parameter.USER_3_SPARE, "0")
        self._param_dict.set_value(Parameter.USER_4_SPARE, "0")

        log.debug("FINISHED SETTING VALUES")




    def _dump_config(self, input):
        # dump config block
        dump = ''
        for byte_index in range(0, len(input)):
            if byte_index % 0x10 == 0:
                if byte_index != 0:
                    dump += '\n'   # no linefeed on first line
                dump += '{:03x}  '.format(byte_index)
            #dump += '0x{:02x}, '.format(ord(input[byte_index]))
            dump += '{:02x} '.format(ord(input[byte_index]))
        return dump

    def _check_configuration(self, input, sync, length):
        log.debug('_check_configuration: config=%s', self._dump_config(input))
        #print self._dump_config(input)
        if len(input) != length+2:
            log.debug('_check_configuration: wrong length, expected length %d != %d' % (length+2, len(input)))
            return False

        # check for ACK bytes
        if input[length:length+2] != InstrumentPrompts.Z_ACK:
            log.debug('_check_configuration: ACK bytes in error %s != %s',
                      input[length:length+2].encode('hex'),
                      InstrumentPrompts.Z_ACK.encode('hex'))
            return False

        # check the sync bytes
        if input[0:4] != sync:
            log.debug('_check_configuration: sync bytes in error %s != %s',
                      input[0:4], sync)
            return False

        # check checksum
        calculated_checksum = NortekProtocolParameterDict.calculate_checksum(input, length)
        log.debug('_check_configuration: user c_c = %s', calculated_checksum)
        sent_checksum = NortekProtocolParameterDict.convert_word_to_int(input[length-2:length])
        if sent_checksum != calculated_checksum:
            log.debug('_check_configuration: user checksum in error %s != %s',
                      calculated_checksum, sent_checksum)
            return False

        return True

    def _update_params(self, *args, **kwargs):
        """
        Update the parameter dictionary. Issue the read config command. The response
        needs to be iterated through a line at a time and values saved to param dictionary.
        @throws InstrumentTimeoutException if device cannot be timely woken.
        @throws InstrumentProtocolException if ds/dc misunderstood.
        """
        if self.get_current_state() != ProtocolState.COMMAND:
            raise InstrumentStateException('Can not perform update of parameters when not in command state')

        log.debug('Sending get_user_configuration command to the instrument.')
        self._handler_command_get_user_config()


    def _get_mode(self, timeout, delay=1):
        """
        _wakeup is replaced by this method for this instrument to search for
        prompt strings at other than just the end of the line.
        @param timeout The timeout to wake the device.
        @param delay The time to wait between consecutive wakeups.
        @throw InstrumentTimeoutException if the device could not be woken.
        """
        # Clear the prompt buffer.
        self._promptbuf = ''

        # Grab time for timeout.
        starttime = time.time()

        log.debug("_get_mode: timeout = %d", timeout)

        while True:
            log.debug('Sending what_mode command to get a response from the instrument.')
            # Send what_mode command to attempt to get a response.
            self._connection.send(InstrumentCmds.SOFT_BREAK_SECOND_HALF)
            self._connection.send(InstrumentCmds.SOFT_BREAK_SECOND_HALF)
            time.sleep(delay)

            for item in self._prompts.list():
                if item in self._promptbuf:
                    if item != InstrumentPrompts.Z_NACK:
                        log.debug('get_mode got prompt: %s' % repr(item))
                        return item

            if time.time() > starttime + timeout:
                raise InstrumentTimeoutException()

    def _create_set_output(self, parameters):
        # load buffer with sync byte (A5), ID byte (0), and size word (# of words in little-endian form)
        # 'user' configuration is 512 bytes, 256 words long, so size is 0x100
        output = '\xa5\x00\x00\x01'
        for name in self.UserParameters:
            log.debug('_create_set_output: adding %s to list', name)
            if name == Parameter.COMMENTS:
                output += parameters.format(name).ljust(180, "\x00")
            elif name == Parameter.DEPLOYMENT_NAME:
                output += parameters.format(name).ljust(6, "\x00")
            elif name == Parameter.QUAL_CONSTANTS:
                output += base64.b64decode(parameters.format(name))
            elif name == Parameter.VELOCITY_ADJ_TABLE:
                output += base64.b64decode(parameters.format(name))
            elif name == Parameter.CLOCK_DEPLOY:
                output += NortekProtocolParameterDict.convert_datetime_to_words(parameters.format(name))
            else:
                output += parameters.format(name)
            log.debug('_create_set_output: ADDED %s to list', name)
        log.debug("Created set output: %r with length: %s", output, len(output))

        checksum = CHECK_SUM_SEED
        for word_index in range(0, len(output), 2):
            word_value = NortekProtocolParameterDict.convert_word_to_int(output[word_index:word_index+2])

            checksum = (checksum + word_value) % 0x10000
            #log.debug('word_index = %s, word_value = %r', word_index, output[word_index:word_index+2])

        log.debug('_create_set_output: user checksum')

        output += NortekProtocolParameterDict.word_to_string(checksum)
        self._dump_config(output)

        return output

    def _build_set_configuration_command(self, cmd, *args, **kwargs):
        user_configuration = kwargs.get('user_configuration', None)
        if not user_configuration:
            raise InstrumentParameterException('set_configuration command missing user_configuration parameter.')
        if not isinstance(user_configuration, str):
            raise InstrumentParameterException('set_configuration command requires a string user_configuration parameter.')
        user_configuration = base64.b64decode(user_configuration)
        self._dump_config(user_configuration)

        cmd_line = cmd + user_configuration
        return cmd_line

    def _build_set_real_time_clock_command(self, cmd, time, **kwargs):
        return cmd + time

    def _parse_read_clock_response(self, response, prompt):
        """ Parse the response from the instrument for a read clock command.

        @param response The response string from the instrument
        @param prompt The prompt received from the instrument
        @retval return The time as a string
        @raise InstrumentProtocolException When a bad response is encountered
        """
        # packed BCD format, so convert binary to hex to get value
        # should be the 6 byte response ending with two ACKs
        if len(response) != 8:
            log.warn("_parse_read_clock_response: Bad read clock response from instrument (%s)", response.encode('hex'))
            raise InstrumentProtocolException("Invalid read clock response. (%s)" % response.encode('hex'))
        log.debug("_parse_read_clock_response: response=%s", response.encode('hex'))

        # Workaround for not so unique data particle chunking
        NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS.append([response, CLK_LEN])

        time = NortekProtocolParameterDict.convert_time(response)
        return time

    # def _parse_what_mode_response(self, response, prompt):
    #     """ Parse the response from the instrument for a 'what mode' command.
    #
    #     @param response The response string from the instrument
    #     @param prompt The prompt received from the instrument
    #     @retval return The time as a string
    #     @raise InstrumentProtocolException When a bad response is encountered
    #     """
    #     if len(response) != 4:
    #         log.warn("_parse_what_mode_response: Bad what mode response from instrument (%s)", response.encode('hex'))
    #         raise InstrumentProtocolException("Invalid what mode response. (%s)" % response.encode('hex'))
    #     log.debug("_parse_what_mode_response: response=%s", response.encode('hex'))
    #     return NortekProtocolParameterDict.convert_word_to_int(response[0:2])

    def _parse_read_battery_voltage_response(self, response, prompt):
        """ Parse the response from the instrument for a read battery voltage command.

        @param response The response string from the instrument
        @param prompt The prompt received from the instrument
        @retval return The time as a string
        @raise InstrumentProtocolException When a bad response is encountered
        """
        if len(response) != BV_LEN:
            log.warn("_parse_read_battery_voltage_response: Bad read battery voltage response from instrument (%s)", response.encode('hex'))
            raise InstrumentProtocolException("Invalid read battery voltage response. (%s)" % response.encode('hex'))
        log.debug("_parse_read_battery_voltage_response: response=%s", response.encode('hex'))

        # Workaround for not so unique data particle chunking
        NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS.append([response, BV_LEN])

        return NortekProtocolParameterDict.convert_word_to_int(response[0:BV_LEN-2])

    def _parse_read_id(self, response, prompt):
        """ Parse the response from the instrument for a read ID command.

        @param response The response string from the instrument
        @param prompt The prompt received from the instrument
        @retval return The time as a string
        @raise InstrumentProtocolException When a bad response is encountered
        """
        if len(response) != ID_LEN:
            log.warn("_handler_command_read_id: Bad read ID response from instrument (%s)", response.encode('hex'))
            raise InstrumentProtocolException("Invalid read ID response. (%s)", response.encode('hex'))
        log.debug("_handler_command_read_id: response=%s", response.encode('hex'))
        return response[0:8]

    # def _parse_sample_average_interval(self, response, prompt):
    #     """ Parse the response from the instrument for a sample average interval command.
    #
    #     @param response The response string from the instrument
    #     @param prompt The prompt received from the instrument
    #     @retval return The time as a string
    #     @raise InstrumentProtocolException When a bad response is encountered
    #     """
    #     if len(response) != INTVL_LEN:
    #         log.warn("_handler_command_sample_average_interval: Bad response from instrument (%s)",
    #                  response.encode('hex'))
    #         raise InstrumentProtocolException("Invalid sample average interval response. (%s)", response.encode('hex'))
    #     log.debug("_handler_command_sample_average_interval: response=%s", response.encode('hex'))
    #
    #     # Workaround for not so unique data particle chunking
    #     # NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS.append([response, INTVL_LEN])
    #
    #     return NortekProtocolParameterDict.convert_word_to_int(response[0:INTVL_LEN-2])
    # def _parse_sample_measurement_interval(self, response, prompt):
    #     """ Parse the response from the instrument for a sample measurement interval command.
    #
    #     @param response The response string from the instrument
    #     @param prompt The prompt received from the instrument
    #     @retval return The time as a string
    #     @raise InstrumentProtocolException When a bad response is encountered
    #     """
    #     if len(response) != INTVL_LEN:
    #         log.warn("_handler_command_sample_measurement_interval: Bad response from instrument (%s)",
    #                  response.encode('hex'))
    #         raise InstrumentProtocolException("Invalid sample measurement interval response. (%s)", response.encode('hex'))
    #     log.debug("_handler_command_sample_measurement_interval: response=%s", response.encode('hex'))
    #
    #     # Workaround for not so unique data particle chunking
    #     # NORTEK_COMMON_DYNAMIC_SAMPLE_STRUCTS.append([response, INTVL_LEN])
    #
    #     return NortekProtocolParameterDict.convert_word_to_int(response[0:INTVL_LEN-2])

    def _parse_read_hw_config(self, response, prompt):
        """ Parse the response from the instrument for a read hw config command.

        @param response The response string from the instrument
        @param prompt The prompt received from the instrument
        @retval return The hardware configuration parse into a dict. Names
        include SerialNo (string), Config (int), Frequency(int),
        PICversion (int), HWrevision (int), RecSize (int), Status (int), and
        FWversion (binary)
        @raise InstrumentProtocolException When a bad response is encountered
        """
        if not self._check_configuration(self._promptbuf, HW_CONFIG_SYNC_BYTES, HW_CONFIG_LEN):
            log.warn("_parse_read_hw_config: Bad read hw response from instrument (%s)", response.encode('hex'))
            raise InstrumentProtocolException("Invalid read hw response. (%s)" % response.encode('hex'))
        log.debug("_parse_read_hw_config: response=%s", response.encode('hex'))

        return hw_config_to_dict(response)

    def _parse_read_head_config(self, response, prompt):
        """ Parse the response from the instrument for a read head command.

        @param response The response string from the instrument
        @param prompt The prompt received from the instrument
        @retval return The head configuration parsed into a dict. Names include
        Config (int), Frequency (int), Type (int), SerialNo (string)
        System (binary), NBeams (int)
        @raise InstrumentProtocolException When a bad response is encountered
        """
        if not self._check_configuration(self._promptbuf, HEAD_CONFIG_SYNC_BYTES, HEAD_CONFIG_LEN):
            log.warn("_parse_read_head_config: Bad read head response from instrument (%s)", response.encode('hex'))
            raise InstrumentProtocolException("Invalid read head response. (%s)" % response.encode('hex'))
        log.debug("_parse_read_head_config: response=%s", response.encode('hex'))

        return head_config_to_dict(response)

    def _parse_read_user_config(self, response, prompt):
        """ Parse the response from the instrument for a read user command.

        @param response The response string from the instrument
        @param prompt The prompt received from the instrument
        @retval return The user configuration parsed into a dict. Names include:

        @raise InstrumentProtocolException When a bad response is encountered
        """
        log.debug("%% IN _parse_read_user_config")
        if not self._check_configuration(self._promptbuf, USER_CONFIG_SYNC_BYTES, USER_CONFIG_LEN):
            log.warn("_parse_read_user_config: Bad read user response from instrument (%s)", response.encode('hex'))
            raise InstrumentProtocolException("Invalid read user response. (%s)" % response.encode('hex'))
        log.debug("_parse_read_user_config: response=%s", response.encode('hex'))

        #return response
        return user_config_to_dict(response)