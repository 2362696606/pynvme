import time
import pytest
import logging

import nvme as d


def test_quarch_dirty_power_cycle_single(nvme0, nvme0n1, subsystem, buf, verify):
    # get the unsafe shutdown count before test
    nvme0.getlogpage(2, buf, 512).waitdone()
    orig_unsafe_count = buf.data(159, 144)
    logging.info("unsafe shutdowns: %d" % orig_unsafe_count)
    assert verify == True

    # 128K random write
    cmdlog_list = [None]*1000
    with nvme0n1.ioworker(io_size=256,
                          lba_random=True,
                          read_percentage=30, 
                          region_end=256*1000*1000,
                          time=30,
                          qdepth=1024, 
                          output_cmdlog_list=cmdlog_list):
        # sudden power loss before the ioworker end
        time.sleep(10)
        subsystem.poweroff()

    # power on and reset controller
    time.sleep(5)
    subsystem.poweron()
    time.sleep(0)
    nvme0.reset()

    # verify unsafe shutdown count
    logging.info(cmdlog_list[-10:])
    nvme0.getlogpage(2, buf, 512).waitdone()
    unsafe_count = buf.data(159, 144)
    logging.info("unsafe shutdowns: %d" % unsafe_count)
    assert unsafe_count == orig_unsafe_count+1

    # verify data in cmdlog_list
    read_buf = d.Buffer(256*512)
    qpair = d.Qpair(nvme0, 1024)
    for cmd in cmdlog_list:
        slba = cmd[0]
        nlba = cmd[1]
        op = cmd[2]
        if nlba:
            def read_cb(cdw0, status1):
                nonlocal slba
                if status1>>1:
                    logging.info("slba 0x%x, status 0x%x" % (slba, status1>>1))
            #logging.info("verify slba 0x%x, nlba %d" % (slba, nlba))
            nvme0n1.read(qpair, read_buf, slba, nlba, cb=read_cb).waitdone()

            
def quarch_dirty_power_cycle_process(pciaddr, repeat):
    nvme0 = d.Controller(d.Pcie(pciaddr))
    nvme0n1 = d.Namespace(nvme0, 1, 128*1000*1000//4)
    subsystem = d.Subsystem(nvme0)
    buf = d.Buffer(4096)

    for i in range(repeat):
        print("test %s loop %d" % (pciaddr, i))
        test_quarch_dirty_power_cycle_single(nvme0, nvme0n1, subsystem, buf, True)

        
def test_quarch_dirty_power_cycle_multiple_processes(repeat=2):
    addr_list = ['3d:00.0']
    #addr_list = ['01:00.0', '03:00.0', '192.168.0.3', '127.0.0.1:4420']

    import multiprocessing
    mp = multiprocessing.get_context("spawn")
    processes = {}
    
    for a in addr_list:
        # create controller and namespace in main process
        nvme0 = d.Controller(d.Pcie(a))
        nvme0n1 = d.Namespace(nvme0, 1, 128*1000*1000//4)
        nvme0n1.verify_enable(True)

        # create subprocess on the device
        p = mp.Process(target = quarch_dirty_power_cycle_process,
                       args = (a, repeat))
        p.start()
        processes[a] = p

    for _, k in enumerate(processes):
        processes[k].join()


# define the power on/off funciton
class quarch_power:
    def __init__(self, url: str, event: str, port: int):
        self.url = url
        self.event = event
        self.port = port
    def __call__(self):
        import quarchpy
        logging.debug("power %s by quarch device %s on port %d" %
                      (self.event, self.url, self.port))
        pwr = quarchpy.quarchDevice(self.url)
        pwr.sendCommand("signal:all:source 7 <%d>" % self.port)
        pwr.sendCommand("run:power %s <%d>" % (self.event, self.port))
        pwr.closeConnection()
        
# test multiple devices one by one in multiple loops with quarch power module
@pytest.mark.parametrize("device", [
    ("01:00.0",
     quarch_power("REST:192.168.1.11", "up", 4),
     quarch_power("REST:192.168.1.11", "down", 4)),
    ("55:00.0",
     quarch_power("REST:192.168.1.11", "up", 1),
     quarch_power("REST:192.168.1.11", "down", 1)),
    ("51:00.0",
     quarch_power("REST:192.168.1.11", "up", 3),
     quarch_power("REST:192.168.1.11", "down", 3)),
])
@pytest.mark.parametrize("repeat", range(2))
def test_quarch_dirty_power_cycle_multiple(pciaddr, repeat, device):
    device = (pciaddr, None, None)  # override by local device test
    
    # run the test one by one
    buf = d.Buffer(4096)
    nvme0 = d.Controller(d.Pcie(device[0]))
    nvme0n1 = d.Namespace(nvme0, 1, 256*1000*1000)
    subsystem = d.Subsystem(nvme0, device[1], device[2])
    assert True == nvme0n1.verify_enable(True)

    # enable inline data verify in the test
    logging.info("testing device %s" % device[0])
    test_quarch_dirty_power_cycle_single(nvme0, nvme0n1, subsystem, buf, True)
    

#TODO: set CC.SHN and dirty shutdown
