#!/usr/bin/env python3
# ========================================================================== #
#                                                                            #
#    KVMD-OLED - Small OLED daemon for Pi-KVM.                               #
#                                                                            #
#    Copyright (C) 2018  Maxim Devaev <mdevaev@gmail.com>                    #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import sys
import socket
import logging
import datetime
import time

from typing import Tuple

import netifaces
import psutil

from luma.core import cmdline as luma_cmdline
from luma.core.device import device as luma_device
from luma.core.render import canvas as luma_canvas

from PIL import Image
from PIL import ImageFont


# =====
_logger = logging.getLogger("oled")


# =====
def _get_ip() -> Tuple[str, str]:
    try:
        gws = netifaces.gateways()
        if "default" not in gws:
            raise RuntimeError(f"No default gateway: {gws}")

        iface = ""
        for proto in [socket.AF_INET, socket.AF_INET6]:
            if proto in gws["default"]:
                iface = gws["default"][proto][1]
                break
        else:
            raise RuntimeError(f"No iface for the gateway {gws['default']}")

        for addr in netifaces.ifaddresses(iface).get(proto, []):
            return (iface, addr["addr"])
    except Exception:
        # _logger.exception("Can't get iface/IP")
        return ("<no-iface>", "<no-ip>")


def _get_uptime() -> str:
    uptime = datetime.timedelta(seconds=int(time.time() - psutil.boot_time()))
    pl = {"days": uptime.days}
    (pl["hours"], rem) = divmod(uptime.seconds, 3600)
    (pl["mins"], pl["secs"]) = divmod(rem, 60)
    return "{days}d {hours}h {mins}m".format(**pl)


def _get_temp(fahrenheit: bool) -> str:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as temp_file:
            temp = int((temp_file.read().strip())) / 1000
            if fahrenheit:
                temp = temp * 9 / 5 + 32
                return f"{temp:.1f}\u00b0F"
            return f"{temp:.1f}\u00b0C"
    except Exception:
        # _logger.exception("Can't read temp")
        return "<no-temp>"


def _get_cpu() -> str:
    st = psutil.cpu_times_percent()
    user = st.user - st.guest
    nice = st.nice - st.guest_nice
    idle_all = st.idle + st.iowait
    system_all = st.system + st.irq + st.softirq
    virtual = st.guest + st.guest_nice
    total = max(1, user + nice + system_all + idle_all + st.steal + virtual)
    percent = int(
        st.nice / total * 100
        + st.user / total * 100
        + system_all / total * 100
        + (st.steal + st.guest) / total * 100
    )
    return f"{percent}%"


def _get_mem() -> str:
    return f"{int(psutil.virtual_memory().percent)}%"


# =====
class Screen:
    def __init__(
        self,
        device: luma_device,
        font: ImageFont.FreeTypeFont,
        font_spacing: int,
        offset: Tuple[int, int],
    ) -> None:

        self.__device = device
        self.__font = font
        self.__font_spacing = font_spacing
        self.__offset = offset
        
    def draw_text(self, text: str) -> None:
        with luma_canvas(self.__device) as draw:
            draw.multiline_text(self.__offset, text, font=self.__font, spacing=self.__font_spacing, fill="white")


    def draw_image(self, image_path: str) -> None:
        with luma_canvas(self.__device) as draw:
            draw.bitmap(self.__offset, Image.open(image_path).convert("1"), fill="white")


# =====
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("PIL").setLevel(logging.ERROR)

    parser = luma_cmdline.create_parser(description="Display FQDN and IP on the OLED")
    parser.add_argument("--font", default="/usr/share/fonts/TTF/ProggySquare.ttf", help="Font path")
    parser.add_argument("--font-size", default=16, type=int, help="Font size")
    parser.add_argument("--font-spacing", default=2, type=int, help="Font line spacing")
    parser.add_argument("--offset-x", default=0, type=int, help="Horizontal offset")
    parser.add_argument("--offset-y", default=0, type=int, help="Vertical offset")
    parser.add_argument("--interval", default=5, type=int, help="Screens interval")
    parser.add_argument("--image", default="", help="Display some image, wait a single interval and exit")
    parser.add_argument("--text", default="", help="Display some text, wait a single interval and exit")
    parser.add_argument("--pipe", action="store_true", help="Read and display lines from stdin until EOF, wait a single interval and exit")
    parser.add_argument("--clear-on-exit", action="store_true", help="Clear display on exit")
    parser.add_argument("--contrast", default=None, type=int, help="Set OLED contrast, values from 0 to 255")
    parser.add_argument("--fahrenheit", action="store_true", help="Display temperature in Fahrenheit instead of Celsius")
    options = parser.parse_args(sys.argv[1:])
    if options.config:
        config = luma_cmdline.load_config(options.config)
        options = parser.parse_args(config + sys.argv[1:])

    device = luma_cmdline.create_device(options)
    device.cleanup = (lambda _: None)
    screen = Screen(
        device=device,
        font=ImageFont.truetype(options.font, options.font_size),
        font_spacing=options.font_spacing,
        offset=(options.offset_x, options.offset_y),
    )

    display_types = luma_cmdline.get_display_types()
    if options.display not in luma_cmdline.get_display_types()["emulator"]:
        _logger.info("Iface: %s", options.interface)
    _logger.info("Display: %s", options.display)
    _logger.info("Size: %dx%d", device.width, device.height)
    if options.contrast is not None:
        options.contrast = min(max(options.contrast, 0), 255)
        _logger.info("Contrast: %d", options.contrast)
        device.contrast(options.contrast)

    try:
        if options.image:
            screen.draw_image(options.image)
            time.sleep(options.interval)

        elif options.text:
            screen.draw_text(options.text.replace("\\n", "\n"))
            time.sleep(options.interval)

        elif options.pipe:
            text = ""
            for line in sys.stdin:
                text += line
                if "\0" in text:
                    screen.draw_text(text.replace("\0", ""))
                    text = ""
            time.sleep(options.interval)

        else:
            summary = True
            while True:
                if summary:
                    text = f"{socket.getfqdn()}\nup: {_get_uptime()}\ntemp: {_get_temp(options.fahrenheit)}"
                else:
                    text = "iface: %s\n~ %s\ncpu: %s mem: %s" % (*_get_ip(), _get_cpu(), _get_mem())
                screen.draw_text(text)
                summary = (not summary)
                time.sleep(max(options.interval, 1))
    except (SystemExit, KeyboardInterrupt):
        pass

    if options.clear_on_exit:
        screen.draw_text("")


if __name__ == "__main__":
    main()
