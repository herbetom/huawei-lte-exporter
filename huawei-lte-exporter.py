#!/usr/bin/env python3

import json
import configparser
import os
import re

from huawei_lte_api.Client import Client
from huawei_lte_api.Connection import Connection
from huawei_lte_api.exceptions import \
    ResponseErrorException, \
    ResponseErrorLoginRequiredException, \
    ResponseErrorNotSupportedException, \
    ResponseErrorSystemBusyException, \
    ResponseErrorLoginCsrfException
from http.client import RemoteDisconnected
from requests.exceptions import ConnectionError

from prometheus_client import start_http_server, Gauge
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily, REGISTRY
import time
from datetime import datetime


def print_dict(text):
    print(json.dumps(text, sort_keys=True, indent=4))


def print_date(text):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ': ' + str(text))


def remove_unit(value: str, unit: str, multiplicator: int = 1) -> float:
    result: float = 0
    unit_multiplicators: dict[str, int] = {
        'kHz': 1000,
        'MHz': 1000000
    }

    if value is not None and value != "None":
        if value.startswith('>='):
            # print('removed >= from value ' + value)
            value = value.replace('>=', '')
        value = value.replace(unit, '')
        result = float(value)
        result *= multiplicator
        result *= unit_multiplicators.get(unit, 1)
    return result


def split_values(value: str):
    result: dict[str, str] = {}
    if value:
        values: list[str] = value.split(' ')
        for i in values:
            temp = i.split(':', 1)
            result[temp[0]] = temp[1]
    return result


config = configparser.ConfigParser()
config.read('config.ini')
MANDATORY_ENV_VARS = ['ROUTER_ADDRESS', 'ROUTER_USER', 'ROUTER_PASS', 'PROM_PORT']
for var in MANDATORY_ENV_VARS:
    if not config.has_option('DEFAULT', var):
        if os.environ.get(var) is None:
            raise EnvironmentError('Failed because {} is not set.'.format(var))
        else:
            print("using {} from Environment because not it wasn't found in config file".format(var))
            config['DEFAULT'][str(var)] = os.environ.get(var)


class HuaweiLteCollector:
    def __init__(self):
        self.url = 'http://' + config['DEFAULT']['ROUTER_ADDRESS'] + '/'
        self.username = config['DEFAULT']['ROUTER_USER']
        self.password = config['DEFAULT']['ROUTER_PASS']

        self.client = None
        self.last_fetch = 0

        self.device_signal = {}
        self.monitoring_traffic_statistics = {}
        self.monitoring_check_notifications = {}
        self.device_information = {}

    def collect(self):
        print_date("access is occurring")
        yield GaugeMetricFamily('start_time', 'Help text', value=time.time())

        device_information_device = GaugeMetricFamily('wwan_device_info', 'Some general infos', labels=['device_name', 'spreadname_en', 'hardware_version', 'software_version'])
        traffic_stats_current_connect_time = GaugeMetricFamily('wwan_traffic_stats_current_connect_time', 'The length of the current connection in seconds')
        traffic_stats_current_download = GaugeMetricFamily('wwan_traffic_stats_current_download', 'The volume of downloaded data during the current connection in Bytes')
        traffic_stats_current_download_rate = GaugeMetricFamily('wwan_traffic_stats_current_download_rate', 'The current download rate in Bytes per Second')
        traffic_stats_current_upload = GaugeMetricFamily('wwan_traffic_stats_current_upload', 'The volume of uploaded data during the current connection in Bytes')
        traffic_stats_current_upload_rate = GaugeMetricFamily('wwan_traffic_stats_current_upload_rate', 'The current upload rate in Bytes per Second')
        traffic_stats_total_connect_time = GaugeMetricFamily('wwan_traffic_stats_total_connect_time', 'The total time connected in seconds')
        traffic_stats_total_download = GaugeMetricFamily('wwan_traffic_stats_total_download', 'The total volume of downloaded data in Bytes')
        traffic_stats_total_upload = GaugeMetricFamily('wwan_traffic_stats_total_upload', 'The total volume of uploaded data in Bytes')

        notifications_sms_storage_full = GaugeMetricFamily('wwan_notifications_sms_storage_full', 'Whether the SMS storage is full')
        notifications_sms_unread_messages = GaugeMetricFamily('wwan_notifications_sms_unread_messages', 'Number of unread SMS')

        device_information_uptime = GaugeMetricFamily('wwan_router_uptime', 'The Router Uptime in seconds')
        device_information_mcc_mnc = GaugeMetricFamily('wwan_router_mcc_mnc', 'MCC (Mobile Country Code) and MNC (Mobile Network Code)')

        device_signal_enodeb_id = GaugeMetricFamily('wwan_signal_enodeb_id', 'The enodeb_id')
        device_signal_cell_id = GaugeMetricFamily('wwan_signal_cell_id', 'The Cell ID')

        device_signal_band = GaugeMetricFamily('wwan_signal_band', 'The Band')
        device_signal_arfcn = GaugeMetricFamily('wwan_signal_arfcn', 'The GSM Absolute Radio Frequency Channel Number', labels=['metric'])

        device_signal_rsrp = GaugeMetricFamily('wwan_signal_rsrp', 'The average power received from a single Reference signal in dBm')
        device_signal_rsrq = GaugeMetricFamily('wwan_signal_rsrq', 'Indicates quality of the received signal in dB')
        device_signal_rssi = GaugeMetricFamily('wwan_signal_rssi', 'Represents the entire received power including the wanted power from the serving cell as well as all co-channel power and other sources of noise in dBm')
        device_signal_rscp = GaugeMetricFamily('wwan_signal_rscp', 'Denotes the power measured by a receiver on a particular physical communication channel in dBm')
        device_signal_sinr = GaugeMetricFamily('wwan_signal_sinr', 'The signal-to-noise ratio of the given signal in dB')
        device_signal_ecio = GaugeMetricFamily('wwan_signal_ecio', 'The EC/IO is a measure of the quality/cleanliness of the signal from the tower to the modem and indicates the signal-to noise ratio in dB')
        device_signal_cqi0 = GaugeMetricFamily('wwan_signal_cqi0', 'The Channel Quality Indicator 0.')
        device_signal_cqi1 = GaugeMetricFamily('wwan_signal_cqi1', 'The Channel Quality Indicator 1.')
        device_signal_downlink_bandwidth = GaugeMetricFamily('wwan_signal_downlink_bandwidth', 'The Downlink Bandwidth in Hz')
        device_signal_downlink_frequency = GaugeMetricFamily('wwan_signal_downlink_frequency', 'The Downlink Frequency in Hz')
        device_signal_uplink_bandwidth = GaugeMetricFamily('wwan_signal_uplink_bandwidth', 'The Uplink Bandwidth in Hz')
        device_signal_uplink_frequency = GaugeMetricFamily('wwan_signal_uplink_frequency', 'The Uplink Frequency in Hz')
        device_signal_downlink_mcs = GaugeMetricFamily('wwan_signal_downlink_mcs','', labels=['carrier', 'code'])
        device_signal_uplink_mcs = GaugeMetricFamily('wwan_signal_uplink_mcs', '', labels=['carrier'])
        device_signal_tx_power = GaugeMetricFamily('wwan_signal_tx_power', 'value in dBm', labels=['metric'])
        device_signal_earfcn = GaugeMetricFamily('wwan_signal_earfcn', 'The LTE Absolute Radio Frequency Channel Number', labels=['metric'])

        device_signal_lte_downlink_frequency = GaugeMetricFamily('wwan_lte_signal_downlink_frequency', 'The LTE Downlink Frequency in Hz')
        device_signal_lte_uplink_frequency = GaugeMetricFamily('wwan_lte_signal_uplink_frequency', 'The LTE Uplink Frequency in Hz')

        device_signal_nr_rsrp = GaugeMetricFamily('wwan_nr_signal_rsrp', 'The average power received from a single Reference 5G NR signal in dBm')
        device_signal_nr_rsrq = GaugeMetricFamily('wwan_nr_signal_rsrq', 'Indicates quality of the received 5G NR signal in dB')
        device_signal_nr_sinr = GaugeMetricFamily('wwan_nr_signal_sinr', 'The signal-to-noise ratio of the given 5G NR signal in dB')
        device_signal_nr_cqi0 = GaugeMetricFamily('wwan_nr_signal_cqi0', 'The 5G NR Channel Quality Indicator 0.')
        device_signal_nr_cqi1 = GaugeMetricFamily('wwan_nr_signal_cqi1', 'The 5G NR Channel Quality Indicator 1.')
        device_signal_nr_downlink_bandwidth = GaugeMetricFamily('wwan_nr_signal_downlink_bandwidth', 'The 5G NR Downlink Bandwidth in Mz')
        device_signal_nr_downlink_frequency = GaugeMetricFamily('wwan_nr_signal_downlink_frequency', 'The 5G NR Downlink Frequency in Hz')
        device_signal_nr_uplink_bandwidth = GaugeMetricFamily('wwan_nr_signal_uplink_bandwidth', 'The 5G NR Uplink Bandwidth in Hz')
        device_signal_nr_uplink_frequency = GaugeMetricFamily('wwan_nr_signal_uplink_frequency', 'The 5G NR Uplink Frequency in Hz')
        device_signal_nr_downlink_mcs = GaugeMetricFamily('wwan_nr_signal_downlink_mcs', '', labels=['carrier', 'code'])
        device_signal_nr_uplink_mcs = GaugeMetricFamily('wwan_nr_signal_uplink_mcs', '', labels=['carrier'])
        device_signal_nr_tx_power = GaugeMetricFamily('wwan_nr_signal_tx_power', 'value in dBm', labels=['metric'])
        device_signal_nr_earfcn = GaugeMetricFamily('wwan_nr_signal_earfcn', 'The 5G NR Absolute Radio Frequency Channel Number', labels=['metric'])

        device_information_device.add_metric([], time.time())

        # fetch new data from router
        self.fetch()

        # populate prom metrics
        device_information_device.add_metric([self.device_information.get('DeviceName'), self.device_information.get('spreadname_en'), self.device_information.get('HardwareVersion'), self.device_information.get('SoftwareVersion')], 1)

        self.set_gauge_from_api(device_information_uptime, self.device_information, 'uptime')
        self.set_gauge_from_api(device_information_mcc_mnc, self.device_information, 'Mccmnc')

        self.set_gauge_from_api(device_signal_cell_id, self.device_signal, 'cell_id')
        self.set_gauge_from_api(device_signal_enodeb_id, self.device_signal, 'enodeb_id')

        self.set_gauge_from_api(device_signal_band, self.device_signal, 'band')

        for name, value in split_values(self.device_signal.get('arfcn')).items():
            device_signal_arfcn.add_metric([name], value)

        self.set_gauge_from_api(device_signal_rsrp, self.device_signal, 'rsrp', 'dBm')
        self.set_gauge_from_api(device_signal_rsrq, self.device_signal, 'rsrq', 'dB')
        self.set_gauge_from_api(device_signal_rssi, self.device_signal, 'rssi', 'dBm')
        self.set_gauge_from_api(device_signal_rscp, self.device_signal, 'rscp', 'dBm')
        self.set_gauge_from_api(device_signal_sinr, self.device_signal, 'sinr', 'dB')
        self.set_gauge_from_api(device_signal_ecio, self.device_signal, 'ecio', 'dB')

        self.set_gauge_from_api(device_signal_cqi0, self.device_signal, 'cqi0')
        self.set_gauge_from_api(device_signal_cqi1, self.device_signal, 'cqi1')

        self.set_gauge_from_api(device_signal_downlink_bandwidth, self.device_signal, 'dlbandwidth', 'MHz')
        self.set_gauge_from_api(device_signal_downlink_frequency, self.device_signal, 'dlfrequency', 'kHz')
        self.set_gauge_from_api(device_signal_uplink_bandwidth, self.device_signal, 'ulbandwidth', 'MHz')
        self.set_gauge_from_api(device_signal_uplink_frequency, self.device_signal, 'ulfrequency', 'kHz')

        for name, value in split_values(self.device_signal.get('dl_mcs')).items():
            carrier_code = re.findall("[a-z]+([0-9]+)", name)
            device_signal_downlink_mcs.add_metric([carrier_code[0], carrier_code[1]], value)
        for name, value in split_values(self.device_signal.get('ul_mcs')).items():
            carrier_code = re.findall("[a-z]+([0-9]+)", name)
            device_signal_uplink_mcs.add_metric([carrier_code[0]], value)
        for name, value in split_values(self.device_signal.get('nrdlmcs')).items():
            carrier_code = re.findall("[a-z]+([0-9]+)", name)
            device_signal_nr_downlink_mcs.add_metric([carrier_code[0], carrier_code[1]], value)
        for name, value in split_values(self.device_signal.get('nrulmcs')).items():
            carrier_code = re.findall("[a-z]+([0-9]+)", name)
            device_signal_nr_uplink_mcs.add_metric([carrier_code[0]], value)

        for name, value in split_values(self.device_signal.get('txpower')).items():
            value = remove_unit(value, "dBm")
            device_signal_tx_power.add_metric([name], value)
        for name, value in split_values(self.device_signal.get('nrtxpower')).items():
            value = remove_unit(value, "dBm")
            device_signal_nr_tx_power.add_metric([name], value)

        self.set_gauge_from_api(device_signal_lte_downlink_frequency, self.device_signal, 'ltedlfreq', 'kHz', 100)
        self.set_gauge_from_api(device_signal_lte_uplink_frequency, self.device_signal, 'lteulfreq', 'kHz', 100)

        for name, value in split_values(self.device_signal.get('earfcn')).items():
            device_signal_earfcn.add_metric([name], value)

        self.set_gauge_from_api(device_signal_nr_rsrp, self.device_signal, 'nrrsrp', 'dBm')
        self.set_gauge_from_api(device_signal_nr_rsrq, self.device_signal, 'nrrsrq', 'dB')
        self.set_gauge_from_api(device_signal_nr_sinr, self.device_signal, 'nrsinr', 'dB')

        self.set_gauge_from_api(device_signal_nr_cqi0, self.device_signal, 'nrcqi0')
        self.set_gauge_from_api(device_signal_nr_cqi1, self.device_signal, 'nrcqi1')
        self.set_gauge_from_api(device_signal_nr_downlink_bandwidth, self.device_signal, 'nrdlbandwidth', 'MHz')
        self.set_gauge_from_api(device_signal_nr_downlink_frequency, self.device_signal, 'nrdlfreq', 'kHz')
        self.set_gauge_from_api(device_signal_nr_uplink_bandwidth, self.device_signal, 'ulbandwidth', 'MHz')
        self.set_gauge_from_api(device_signal_nr_uplink_frequency, self.device_signal, 'ulfrequency', 'kHz')

        for name, value in split_values(self.device_signal.get('nrearfcn')).items():
            device_signal_nr_earfcn.add_metric([name], value)

        self.set_gauge_from_api(traffic_stats_current_connect_time, self.monitoring_traffic_statistics, 'CurrentConnectTime')
        self.set_gauge_from_api(traffic_stats_current_download, self.monitoring_traffic_statistics, 'CurrentDownload')
        self.set_gauge_from_api(traffic_stats_current_download_rate, self.monitoring_traffic_statistics, 'CurrentDownloadRate')
        self.set_gauge_from_api(traffic_stats_current_upload, self.monitoring_traffic_statistics, 'CurrentUpload')
        self.set_gauge_from_api(traffic_stats_current_upload_rate, self.monitoring_traffic_statistics, 'CurrentUploadRate')
        self.set_gauge_from_api(traffic_stats_total_connect_time, self.monitoring_traffic_statistics, 'TotalConnectTime')
        self.set_gauge_from_api(traffic_stats_total_download, self.monitoring_traffic_statistics, 'TotalDownload')
        self.set_gauge_from_api(traffic_stats_total_upload, self.monitoring_traffic_statistics, 'TotalUpload')

        self.set_gauge_from_api(notifications_sms_storage_full, self.monitoring_check_notifications, 'SmsStorageFull')
        self.set_gauge_from_api(notifications_sms_unread_messages, self.monitoring_check_notifications, 'UnreadMessage')

        yield device_information_device
        yield traffic_stats_current_connect_time
        yield traffic_stats_current_download
        yield traffic_stats_current_download_rate
        yield traffic_stats_current_upload
        yield traffic_stats_current_upload_rate
        yield traffic_stats_total_connect_time
        yield traffic_stats_total_download
        yield traffic_stats_total_upload
        yield notifications_sms_storage_full
        yield notifications_sms_unread_messages
        yield device_information_uptime
        yield device_information_mcc_mnc
        yield device_signal_enodeb_id
        yield device_signal_cell_id
        yield device_signal_band
        yield device_signal_arfcn
        yield device_signal_rsrp
        yield device_signal_rsrq
        yield device_signal_rssi
        yield device_signal_rscp
        yield device_signal_sinr
        yield device_signal_ecio
        yield device_signal_cqi0
        yield device_signal_cqi1
        yield device_signal_downlink_bandwidth
        yield device_signal_downlink_frequency
        yield device_signal_uplink_bandwidth
        yield device_signal_uplink_frequency
        yield device_signal_downlink_mcs
        yield device_signal_uplink_mcs
        yield device_signal_tx_power
        yield device_signal_lte_downlink_frequency
        yield device_signal_lte_uplink_frequency
        yield device_signal_earfcn
        yield device_signal_nr_rsrp
        yield device_signal_nr_rsrq
        yield device_signal_nr_sinr
        yield device_signal_nr_cqi0
        yield device_signal_nr_cqi1
        yield device_signal_nr_downlink_bandwidth
        yield device_signal_nr_downlink_frequency
        yield device_signal_nr_uplink_bandwidth
        yield device_signal_nr_uplink_frequency
        yield device_signal_nr_downlink_mcs
        yield device_signal_nr_uplink_mcs
        yield device_signal_nr_tx_power
        yield device_signal_nr_earfcn

    def fetch(self):
        current_time = time.time()
        if current_time - self.last_fetch >= 4:
            # get data from Router
            try:
                with Connection(self.url, username=self.username, password=self.password) as connection:
                    self.client = Client(connection)
                    self.monitoring_traffic_statistics = self.client.monitoring.traffic_statistics()
                    self.monitoring_check_notifications = self.client.monitoring.check_notifications()

                    self.device_information = self.client.device.information()
                    self.device_signal = self.client.device.signal()

            # except Exception as e:
            except ResponseErrorLoginRequiredException as e:
                print_date("Login error " + str(e))
            except ResponseErrorException as e:
                print_date("other error " + str(e))
            except RemoteDisconnected as e:
                print_date("remote disconnected " + str(e))
            except ConnectionError as e:
                print_date("connection error " + str(e))
            else:
                # print_date("updated cached data and last_fetch time")
                self.last_fetch = current_time
        else:
            pass
            # print_date("using cached data")

    def set_gauge_from_api(self, set_value, from_value, api_value_name, unit_to_remove="", multiplicator=1, labels=None):

        if labels is None:
            labels = []
        value = from_value.get(api_value_name)
        if value is None:
            value = 0
        if unit_to_remove != "":
            value = remove_unit(str(value), unit_to_remove,multiplicator)
        set_value.add_metric(labels, value)


def main():
    print_date("Starting")

    print_date("Initializing and first fetch")
    REGISTRY.register(HuaweiLteCollector())

    print_date("Starting WebServer on Port {} ".format(config['DEFAULT']['PROM_PORT']))
    start_http_server(int(config['DEFAULT']['PROM_PORT']),addr='::')

    while True:
        time.sleep(30)


if __name__ == '__main__':
    main()
