from data_frame_factory import DataFrameFactory
from modem.modem import RF
import queue
from codec2 import FREEDV_MODE

class TxCommand():

    def __init__(self, config, logger, apiParams):
        self.config = config
        self.logger = logger
        self.set_params_from_api(apiParams)
        self.frame_factory = DataFrameFactory()

    def set_params_from_api(self, apiParams):
        pass

    def get_name(self):
        return type(self).__name__

    def emit_event(self):
        pass

    def log_message(self):
        return f"TX Command {self.get_name()}"

    def build_frame(self):
        pass

    def transmit(self, tx_frame_queue):
        frame = self.build_frame()
        c2_mode = FREEDV_MODE.fsk_ldpc_0.value if self.config.enable_fsk else FREEDV_MODE.sig0.value
        tx_queue_item = [c2_mode, 1, 0, frame]
        tx_frame_queue.put(tx_queue_item)

    def run(self, tx_frame_queue: queue.Queue):
        self.emit_event()
        self.logger.info(self.log_message)
        self.transmit(tx_frame_queue)
        pass
