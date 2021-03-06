#!/usr/bin/env python

"""
@package mi.dataset.parser.test.test_ctdpf_ckl_wfp_sio_mule_yml_build
@file marine-integrations/mi/dataset/parser/test/test_ctdpf_ckl_wfp_sio_mule_yml_build.py
@author cgoodrich
@brief Test code for a ctdpf_ckl_wfp_sio_mule data parser
"""
import os
import struct
import ntplib
from StringIO import StringIO

from nose.plugins.attrib import attr

from mi.core.log import get_logger
log = get_logger()
from mi.idk.config import Config

from mi.dataset.test.test_parser import ParserUnitTestCase
from mi.dataset.dataset_driver import DataSetDriverConfigKeys
from mi.dataset.parser.ctdpf_ckl_wfp import CtdpfCklWfpParser
from mi.dataset.driver.ctdpf_ckl.wfp.driver import DataTypeKey
from mi.dataset.parser.ctdpf_ckl_wfp_particles import CtdpfCklWfpRecoveredDataParticle
from mi.dataset.parser.ctdpf_ckl_wfp_particles import CtdpfCklWfpTelemeteredDataParticle
from mi.dataset.parser.ctdpf_ckl_wfp_particles import CtdpfCklWfpRecoveredMetadataParticle
from mi.dataset.parser.ctdpf_ckl_wfp_particles import CtdpfCklWfpTelemeteredMetadataParticle
from mi.dataset.parser.wfp_c_file_common import StateKey
from mi.dataset.parser.ctdpf_ckl_wfp_particles import DataParticleType
from mi.dataset.parser.ctdpf_ckl_wfp_particles import CtdpfCklWfpDataParticleKey


RESOURCE_PATH = os.path.join(Config().base_dir(), 'mi',
                             'dataset', 'driver', 'ctdpf_ckl',
                             'wfp', 'resource')


@attr('UNIT', group='mi')
class CtdpfCklWfpParserUnitTestCase(ParserUnitTestCase):
    """
    ctdpf_ckl_wfp_sio_mule Parser unit test suite
    """
    recovered_start_state = {StateKey.POSITION: 0,
                                  StateKey.RECORDS_READ: 0,
                                  StateKey.METADATA_SENT: False}

    telemetered_start_state = {StateKey.POSITION: 0,
                                    StateKey.RECORDS_READ: 0,
                                    StateKey.METADATA_SENT: False}

    def state_callback(self, state, file_ingested):
        """ Call back method to watch what comes in via the position callback """
        self.file_ingested_value = file_ingested
        state = None

    def pub_callback(self, pub):
        """ Call back method to watch what comes in via the publish callback """
        self.publish_callback_value = pub

    def exception_callback(self, exception):
        """ Callback method to watch what comes in via the exception callback """
        self.exception_callback_value = exception

    def setUp(self):

        ParserUnitTestCase.setUp(self)

        self.config = {
            DataTypeKey.CTDPF_CKL_WFP_RECOVERED: {
                DataSetDriverConfigKeys.PARTICLE_MODULE: 'mi.dataset.parser.ctdpf_ckl_wfp',
                DataSetDriverConfigKeys.PARTICLE_CLASS: None,
                'particle_classes_dict': {
                    'instrument_data_particle_class': CtdpfCklWfpRecoveredDataParticle,
                    'metadata_particle_class': CtdpfCklWfpRecoveredMetadataParticle
                },
            },
            DataTypeKey.CTDPF_CKL_WFP_TELEMETERED: {
                DataSetDriverConfigKeys.PARTICLE_MODULE: 'mi.dataset.parser.ctdpf_ckl_wfp',
                DataSetDriverConfigKeys.PARTICLE_CLASS: None,
                'particle_classes_dict': {
                    'instrument_data_particle_class': CtdpfCklWfpTelemeteredDataParticle,
                    'metadata_particle_class': CtdpfCklWfpTelemeteredMetadataParticle
                }
            }
        }

        self.file_ingested_value = None
        self.state_callback_value = None
        self.publish_callback_value = None

    def calc_timestamp(self, start, increment, sample_idx):
        new_time = start + (increment * sample_idx)
        return float(ntplib.system_to_ntp_time(new_time))

    def assert_result(self, result, particle, ingested):
        self.assertEqual(result, [particle])
        self.assertEqual(self.file_ingested_value, ingested)
        self.assert_(isinstance(self.publish_callback_value, list))
        self.assertEqual(self.publish_callback_value[0], particle)

    def particle_to_yml(self, particles, filename, mode='w'):
        """
        This is added as a testing helper, not actually as part of the parser tests. Since the same particles
        will be used for the driver test it is helpful to write them to .yml in the same form they need in the
        results.yml fids here.
        """
        # open write append, if you want to start from scratch manually delete this fid
        fid = open(os.path.join(RESOURCE_PATH, filename), mode)

        fid.write('header:\n')
        fid.write("    particle_object: 'MULTIPLE'\n")
        fid.write("    particle_type: 'MULTIPLE'\n")
        fid.write('data:\n')

        for i in range(0, len(particles)):
            particle_dict = particles[i].generate_dict()

            fid.write('  - _index: %d\n' %(i+1))

            fid.write('    particle_object: %s\n' % particles[i].__class__.__name__)
            fid.write('    particle_type: %s\n' % particle_dict.get('stream_name'))
            fid.write('    internal_timestamp: %f\n' % particle_dict.get('internal_timestamp'))

            for val in particle_dict.get('values'):
                if isinstance(val.get('value'), float):
                    fid.write('    %s: %16.16f\n' % (val.get('value_id'), val.get('value')))
                else:
                    fid.write('    %s: %s\n' % (val.get('value_id'), val.get('value')))
        fid.close()

    def test_build_yml_file(self):
        """
        Read test data. Should detect that there is a decimation factor in the data.
        Check that the data matches the expected results.
        """
        log.debug('CAG TEST: START BUILDING YML FILES')

        stream_handle = open('/home/cgoodrich/Workspace/code/marine-integrations/mi/dataset/driver/ctdpf_ckl/wfp_sio_mule/resource/BIG_C0000038.dat', 'rb')
        filesize = os.path.getsize(stream_handle.name)
        self.recovered_parser = CtdpfCklWfpParser(
            self.config.get(DataTypeKey.CTDPF_CKL_WFP_RECOVERED), self.recovered_start_state, stream_handle,
            self.state_callback, self.pub_callback, self.exception_callback, filesize)
        result = self.recovered_parser.get_records(50000)
        log.debug('CAG Number of Results %d', len(result))
        self.particle_to_yml(result, 'BIG_C0000038.yml')
        stream_handle.close()

#        stream_handle = open('/home/cgoodrich/Workspace/code/marine-integrations/mi/dataset/driver/ctdpf_ckl/wfp/resource/first.DAT', 'rb')
#        filesize = os.path.getsize(stream_handle.name)
#        self.recovered_parser = CtdpfCklWfpParser(
#            self.config.get(DataTypeKey.CTDPF_CKL_WFP_RECOVERED), self.recovered_start_state, stream_handle,
#            self.state_callback, self.pub_callback, self.exception_callback, filesize)
#        result = self.recovered_parser.get_records(4)
#        log.debug('CAG Number of Results %d', len(result))
#        self.particle_to_yml(result, 'first.result.yml')
#        stream_handle.close()

#        stream_handle = open('/home/cgoodrich/Workspace/code/marine-integrations/mi/dataset/driver/ctdpf_ckl/wfp/resource/C0000038.DAT', 'rb')
#        filesize = os.path.getsize(stream_handle.name)
#        self.telemetered_parser = CtdpfCklWfpParser(
#            self.config.get(DataTypeKey.CTDPF_CKL_WFP_TELEMETERED), self.telemetered_start_state, stream_handle,
#            self.state_callback, self.pub_callback, self.exception_callback, filesize)
#        result = self.telemetered_parser.get_records(272)
#        log.debug('CAG Number of Results %d', len(result))
#        self.particle_to_yml(result, 'C0000038.yml')
#        stream_handle.close()

#        stream_handle = open('/home/cgoodrich/Workspace/code/marine-integrations/mi/dataset/driver/ctdpf_ckl/wfp/resource/second.DAT', 'rb')
#        filesize = os.path.getsize(stream_handle.name)
#        self.telemetered_parser = CtdpfCklWfpParser(
#            self.config.get(DataTypeKey.CTDPF_CKL_WFP_TELEMETERED), self.telemetered_start_state, stream_handle,
#            self.state_callback, self.pub_callback, self.exception_callback, filesize)
#        result = self.telemetered_parser.get_records(7)
#        log.debug('CAG Number of Results %d', len(result))
#        self.particle_to_yml(result, 'second.result.yml')
#        stream_handle.close()

        log.debug('CAG TEST: FINISHED BUILDING YML FILES')

