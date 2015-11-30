#!/usr/bin/env python3
import argparse
import os

from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen.fhdl.specials import Keep

from litex.boards.platforms import nexys_video as nexys

from litex.soc.integration.soc_core import mem_decoder
from litex.soc.integration.soc_sdram import *
from litex.soc.cores.sdram.settings import SDRAMModule
from litex.soc.integration.builder import *

from liteeth.phy.s7rgmii import LiteEthPHYRGMII
from liteeth.core.mac import LiteEthMAC

from cores import a7ddrphy, dna, xadc

# TODO: use half-rate DDR3 phy and use 100Mhz CPU clock

class MT41K256M16(SDRAMModule):
    geom_settings = {
        "nbanks": 8,
        "nrows":  32768,
        "ncols":  1024,
    }
    timing_settings = {
        "tRP":   13.125,
        "tRCD":  13.125,
        "tWR":   15,
        "tWTR":  4,
        "tREFI": 64*1000*1000/8192,
        "tRFC":  260,
    }

    def __init__(self, clk_freq):
        SDRAMModule.__init__(self, clk_freq, "DDR3", self.geom_settings,
                             self.timing_settings)


class _CRG(Module):
    def __init__(self, platform):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys4x = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys4x_dqs = ClockDomain(reset_less=True)
        self.clock_domains.cd_clk200 = ClockDomain()

        clk100 = platform.request("clk100")
        rst = platform.request("cpu_reset")

        pll_locked = Signal()
        pll_fb = Signal()
        self.pll_sys = Signal()
        pll_sys4x = Signal()
        pll_sys4x_dqs = Signal()
        pll_clk200 = Signal()
        self.specials += [
            Instance("PLLE2_BASE",
                     p_STARTUP_WAIT="FALSE", o_LOCKED=pll_locked,

                     # VCO @ 800 MHz
                     p_REF_JITTER1=0.01, p_CLKIN1_PERIOD=10.0,
                     p_CLKFBOUT_MULT=8, p_DIVCLK_DIVIDE=1,
                     i_CLKIN1=clk100, i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb,

                     # 50 MHz
                     p_CLKOUT0_DIVIDE=8, p_CLKOUT0_PHASE=0.0,
                     o_CLKOUT0=self.pll_sys,

                     # 200 MHz
                     p_CLKOUT1_DIVIDE=4, p_CLKOUT1_PHASE=0.0,
                     o_CLKOUT1=pll_sys4x,

                     # 200 MHz dqs
                     p_CLKOUT2_DIVIDE=4, p_CLKOUT2_PHASE=135.0,
                     o_CLKOUT2=pll_sys4x_dqs,

                     # 200 MHz
                     p_CLKOUT3_DIVIDE=4, p_CLKOUT3_PHASE=0.0,
                     o_CLKOUT3=pll_clk200,

                     # 200MHz
                     p_CLKOUT4_DIVIDE=4, p_CLKOUT4_PHASE=0.0,
                     #o_CLKOUT4=
            ),
            Instance("BUFG", i_I=self.pll_sys, o_O=self.cd_sys.clk),
            Instance("BUFG", i_I=pll_sys4x, o_O=self.cd_sys4x.clk),
            Instance("BUFG", i_I=pll_sys4x_dqs, o_O=self.cd_sys4x_dqs.clk),
            Instance("BUFG", i_I=pll_clk200, o_O=self.cd_clk200.clk),
            AsyncResetSynchronizer(self.cd_sys, ~pll_locked | ~rst),
            AsyncResetSynchronizer(self.cd_clk200, ~pll_locked | rst),
        ]

        reset_counter = Signal(4, reset=15)
        ic_reset = Signal(reset=1)
        self.sync.clk200 += \
            If(reset_counter != 0,
                reset_counter.eq(reset_counter - 1)
            ).Else(
                ic_reset.eq(0)
            )
        self.specials += Instance("IDELAYCTRL", i_REFCLK=ClockSignal("clk200"), i_RST=ic_reset)


class BaseSoC(SoCSDRAM):
    csr_map = {
        "ddrphy":   17,
        "dna":      18,
        "xadc":     19
    }
    csr_map.update(SoCSDRAM.csr_map)

    def __init__(self,
                 integrated_rom_size=0x8000,
                 integrated_main_ram_size=0x8000,
                 sdram_controller_type="minicon",
                 **kwargs):
        platform = nexys.Platform()
        SoCSDRAM.__init__(self, platform,
                          clk_freq=100*1000000,
                          integrated_rom_size=integrated_rom_size,
                          integrated_main_ram_size=integrated_main_ram_size,
                          **kwargs)

        self.submodules.crg = _CRG(platform)
        self.submodules.dna = dna.DNA()
        self.submodules.xadc = xadc.XADC()

        if not self.integrated_main_ram_size:
            self.submodules.ddrphy = a7ddrphy.A7DDRPHY(platform.request("ddram"))
            sdram_module = MT41K256M16(self.clk_freq)
            self.register_sdram(self.ddrphy, sdram_controller_type,
                                sdram_module.geom_settings, sdram_module.timing_settings)


class MiniSoC(BaseSoC):
    csr_map = {
        "ethphy": 30,
        "ethmac": 31
    }
    csr_map.update(BaseSoC.csr_map)

    interrupt_map = {
        "ethmac": 2,
    }
    interrupt_map.update(BaseSoC.interrupt_map)

    mem_map = {
        "ethmac": 0x30000000,  # (shadow @0xb0000000)
    }
    mem_map.update(BaseSoC.mem_map)

    def __init__(self, **kwargs):
        BaseSoC.__init__(self, **kwargs)

        self.submodules.ethphy = LiteEthPHYRGMII(self.platform.request("eth_clocks"),
                                                 self.platform.request("eth"))
        self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32, interface="wishbone")
        self.add_wb_slave(mem_decoder(self.mem_map["ethmac"]), self.ethmac.bus)
        self.add_memory_region("ethmac", self.mem_map["ethmac"] | self.shadow_base, 0x2000)

        self.configure_ip("localip", [192, 168, 1, 42])
        self.configure_ip("remoteip", [192, 168, 1, 10])

        self.specials += [
            Keep(self.ethphy.crg.cd_eth_rx.clk),
            Keep(self.ethphy.crg.cd_eth_tx.clk)
        ]
        self.platform.add_platform_command("""
create_clock -name sys_clk -period 10 [get_nets sys_clk]
create_clock -name eth_rx_clk -period 8 [get_nets eth_clocks_tx]
create_clock -name eth_tx_clk -period 8 [get_nets eth_clocks_rx]

set_false_path -from [get_clocks eth_rx_clk] -to [get_clocks sys_clk]
set_false_path -from [get_clocks sys_clk] -to [get_clocks eth_rx_clk]
set_false_path -from [get_clocks eth_tx_clk] -to [get_clocks sys_clk]
set_false_path -from [get_clocks sys_clk] -to [get_clocks eth_tx_clk]
""")

    def configure_ip(self, ip_type, ip):
        for i, e in enumerate(ip):
            s = ip_type + str(i + 1)
            s = s.upper()
            self.add_constant(s, e)

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC port to Arty")
    builder_args(parser)
    soc_sdram_args(parser)
    parser.add_argument("--with-ethernet", action="store_true",
                        help="enable Ethernet support")
    parser.add_argument("--build", action="store_true",
                        help="build bitstream")
    parser.add_argument("--load", action="store_true",
                        help="load bitstream")
    args = parser.parse_args()

    cls = MiniSoC if args.with_ethernet else BaseSoC
    soc = cls(**soc_sdram_argdict(args))
    builder = Builder(soc, **builder_argdict(args))

    if args.build:
        builder.build()

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.output_dir, "gateware", "top.bit"))


if __name__ == "__main__":
    main()
