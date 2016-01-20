# Copyright 2016 Pavel Milanes CO7WT, <co7wt@frcuba.co.cu> <pavelmc@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import time
import struct
import logging

LOG = logging.getLogger(__name__)

from chirp import chirp_common, directory, memmap
from chirp import bitwise, errors, util
from chirp.settings import RadioSettingGroup, RadioSetting, \
    RadioSettingValueBoolean, RadioSettingValueList, \
    RadioSettingValueString, RadioSettingValueInteger, \
    RadioSettings
from textwrap import dedent

MEM_FORMAT = """
#seekto 0x0000;
struct {
  lbcd rxfreq[4];
  lbcd txfreq[4];
} memory[32];

#seekto 0x0100;
struct {
  lbcd rx_tone[2];
  lbcd tx_tone[2];
} tone[32];

#seekto 0x0180;
struct {
  u8 unknown0:1,
     unknown1:1,
     wide:1,            // wide: 1 = wide, 0 = narrow
     power:1,           // power: 1 = high, 0 = low
     busy_lock:1,       // busy lock:  1 = off, 0 = on
     pttid:1,           // ptt id:  1 = off, 0 = on
     dtmf:1,            // dtmf signaling:  1 = off, 0 = on
     twotone:1;         // 2-tone signaling:  1 = off, 0 = on
} ch_settings[32];

#seekto 0x02B0;
struct {
    u8 unknown10[16];      // x02b0
    u8 unknown11[16];      // x02c0
    u8 active[4];            // x02d0
    u8 scan[4];              // x02d4
    u8 unknown12[8];         // x02d8
    u8 unknown13;          // x02e0
    u8 kMON;                 // 0x02d1 MON Key
    u8 kA;                   // 0x02d2 A Key
    u8 kSCN;                 // 0x02d3 SCN Key
    u8 kDA;                  // 0x02d4 D/A Key
    u8 unknown14;            // x02e5
    u8 min_vol;              // x02e6 byte 0-31 0 = off
    u8 poweron_tone;         // x02e7 power on tone 0 = off, 1 = on
    u8 tot;                  // x02e8 Time out Timer 0 = off, 1 = 30s (max 300)
    u8 unknown15[3];         // x02e9-x02eb
    u8 dealer_tuning;        // x02ec ? bit 0? 0 = off, 1 = on
    u8 clone;                // x02ed ? bit 0? 0 = off, 1 = on
    u8 unknown16[2];         // x02ee-x2ef
    u8 unknown17[16];      // x02f0
    u8 unknown18[5];       // x0300
    u8 clear2transpond;      // x0305 byte 0 = off, 1 = on
    u8 off_hook_decode;      // x0306 byte 0 = off, 1 = on
    u8 off_hook_hornalert;   // x0307 byte 0 = off, 1 = on
    u8 unknown19[8];         // x0308-x030f
    u8 unknown20[16];      // x0310
} settings;
"""

KEYS = {
    0x00: "Disabled",
    0x01: "Monitor",
    0x02: "Talk Around",
    0x03: "Horn Alert",
    0x04: "Public Adress",
    0x05: "Auxiliary",
    0x06: "Scan",
    0x07: "Scan Del/Add",
    0x08: "Home Channel",
    0x09: "Operator Selectable Tone"
}

MEM_SIZE = 0x400
BLOCK_SIZE = 8
MEM_BLOCKS = range(0, (MEM_SIZE / BLOCK_SIZE))
ACK_CMD = "\x06"
TIMEOUT = 0.05  # from 0.03 up it' s safe, we set in 0.05 for a margin

POWER_LEVELS = [chirp_common.PowerLevel("Low", watts=1),
                chirp_common.PowerLevel("High", watts=5)]
MODES = ["NFM", "FM"]
SKIP_VALUES = ["", "S"]
TONES = chirp_common.TONES
#TONES.remove(254.1)
DTCS_CODES = chirp_common.DTCS_CODES

TOT = ["off"] + ["%s" % x for x in range(30, 330, 30)]
VOL = ["off"] + ["%s" % x for x in range(1, 32)]


def rawrecv(radio, amount):
    """Raw read from the radio device"""
    data = ""
    try:
        data = radio.pipe.read(amount)
        #print("<= %02i: %s" % (len(data), util.hexprint(data)))
    except:
        raise errors.RadioError("Error reading data from radio")

    return data


def rawsend(radio, data):
    """Raw send to the radio device"""
    try:
        radio.pipe.write(data)
        #print("=> %02i: %s" % (len(data), util.hexprint(data)))
    except:
        raise errors.RadioError("Error sending data from radio")


def send(radio, frame):
    """Generic send data to the radio"""
    rawsend(radio, frame)


def make_frame(cmd, addr, data=""):
    """Pack the info in the format it likes"""
    ts = struct.pack(">BHB", ord(cmd), addr, 8)
    if data == "":
        return ts
    else:
        if len(data) == 8:
            return ts + data
        else:
            raise errors.InvalidValueError("Data length of unexpected length")


def handshake(radio, msg="", full=False):
    """Make a full handshake, if not full just hals"""
    # send ACK if commandes
    if full is True:
        rawsend(radio, ACK_CMD)
    # receive ACK
    ack = rawrecv(radio, 1)
    # check ACK
    if ack != ACK_CMD:
        #close_radio(radio)
        mesg = "Handshake failed: " + msg
        raise errors.RadioError(mesg)


def recv(radio):
    """Receive data from the radio, 12 bytes, 4 in the header, 8 as data"""
    rxdata = rawrecv(radio, 12)

    if len(rxdata) != 12:
        raise errors.RadioError(
            "Received a length of data that is not possible")
        return

    cmd, addr, length = struct.unpack(">BHB", rxdata[0:4])
    data = ""
    if length == 8:
        data = rxdata[4:]

    return data


def open_radio(radio):
    """Open the radio into program mode and check if it's the correct model"""
    # Set serial discipline
    try:
        radio.pipe.setParity("N")
        radio.pipe.timeout = TIMEOUT
        radio.pipe.flushOutput()
        radio.pipe.flushInput()
    except:
        msg = "Serial error: Can't set serial line discipline"
        raise errors.RadioError(msg)

    magic = "PROGRAM"
    for i in range(0, len(magic)):
        ack = rawrecv(radio, 1)
        time.sleep(0.05)
        send(radio, magic[i])

    handshake(radio, "Radio not entering Program mode")
    rawsend(radio, "\x02")
    ident = rawrecv(radio, 8)
    handshake(radio, "Comm error after ident", True)

    if not (radio.TYPE in ident):
        LOG.debug("Incorrect model ID, got %s" % util.hexprint(ident))
        msg = "Incorrect model ID, got %s, it not contains %s" % \
            (ident[0:5], radio.TYPE)
        raise errors.RadioError(msg)

    # DEBUG
    #print("Full ident string is %s" % util.hexprint(ident))

    # this is needed, I don't know why, yet
    send(radio, make_frame("W", 0x03e1, "\xff\x01" + "\xff" * 6))
    handshake(radio, "Comm error  after setup", True)


def do_download(radio):
    """This is your download function"""
    open_radio(radio)

     # UI progress
    status = chirp_common.Status()
    status.cur = 0
    status.max = MEM_SIZE / BLOCK_SIZE
    status.msg = "Cloning from radio..."
    radio.status_fn(status)

    data = ""
    for addr in MEM_BLOCKS:
        send(radio, make_frame("R", addr * BLOCK_SIZE))
        data += recv(radio)
        handshake(radio, "Rx error in block %03i" % addr, True)
        # DEBUG
        #print("Block: %04x, Pos: %06x" % (addr, addr * BLOCK_SIZE))

        # UI Update
        status.cur = addr
        status.msg = "Cloning from radio..."
        radio.status_fn(status)

    return memmap.MemoryMap(data)


def do_upload(radio):
    """Upload info to radio"""
    open_radio(radio)

     # UI progress
    status = chirp_common.Status()
    status.cur = 0
    status.max = MEM_SIZE / BLOCK_SIZE
    status.msg = "Cloning to radio..."
    radio.status_fn(status)
    count = 0

    for addr in MEM_BLOCKS:
        # UI Update
        status.cur = addr
        status.msg = "Cloning to radio..."
        radio.status_fn(status)

        block = addr * BLOCK_SIZE
        if block > 0x0378:
            # it seems that from this point forward is read only !?!?!?
            continue

        send(radio, make_frame("W", block,
                               radio.get_mmap()[block:block + BLOCK_SIZE]))

        # DEBUG
        #print("Block: %04x, Pos: %04x" % (addr, addr * BLOCK_SIZE))

        time.sleep(0.1)
        handshake(radio, "Rx error in block %03i" % addr)


def get_rid(data):
    """Extract the radio identification from the firmware"""
    rid = data[0x0378:0x0380]
    # we have to invert rid
    nrid = ""
    for i in range(1, len(rid) + 1):
        nrid += rid[-i]
    rid = nrid

    return rid


def model_match(cls, data):
    """Match the opened/downloaded image to the correct version"""
    rid = get_rid(data)

    # DEBUG
    #print("Full ident string is %s" % util.hexprint(rid))

    if (rid in cls.VARIANTS):
        # correct model
        return True
    else:
        return False


class Kenwood_M60_Radio(chirp_common.CloneModeRadio):
    """Kenwood Mobile Family 60 Radios"""
    VENDOR = "Kenwood"
    _range = [350000000, 500000000]  # don't mind, it will be overited
    _upper = 32
    VARIANT = ""
    MODEL = ""

    @classmethod
    def get_prompts(cls):
        rp = chirp_common.RadioPrompts()
        rp.experimental = \
            ('This driver is experimental and for personal use only.'
             'It has a limited set of features, but the most used.\n'
             '\n'
             'The most notorius missing features are this:\n'
             '=> PTT ID, in fact it is disabled if detected\n'
             '=> Priority / Home channel\n'
             '=> DTMF/2-Tone\n'
             '=> Others\n'
             '\n'
             'If you need one of this, get your official software to do it'
             'and raise and issue on the chirp site about it; maybe'
             'it will be implemented in the future.'
             )
        rp.pre_download = _(dedent("""\
            Follow this instructions to download your info:
            1 - Turn off your radio
            2 - Connect your interface cable
            3 - Do the download of your radio data
            """))
        rp.pre_upload = _(dedent("""\
            Follow this instructions to download your info:
            1 - Turn off your radio
            2 - Connect your interface cable
            3 - Do the upload of your radio data
            """))
        return rp

    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_settings = True
        rf.has_bank = False
        rf.has_tuning_step = False
        rf.has_name = False
        rf.has_offset = True
        rf.has_mode = True
        rf.has_dtcs = True
        rf.has_rx_dtcs = True
        rf.has_dtcs_polarity = True
        rf.has_ctone = True
        rf.has_cross = True
        rf.valid_modes = MODES
        rf.valid_duplexes = ["", "-", "+", "off"]
        rf.valid_tmodes = ['', 'Tone', 'TSQL', 'DTCS', 'Cross']
        rf.valid_cross_modes = [
            "Tone->Tone",
            "DTCS->",
            "->DTCS",
            "Tone->DTCS",
            "DTCS->Tone",
            "->Tone",
            "DTCS->DTCS"]
        rf.valid_power_levels = POWER_LEVELS
        rf.valid_skips = SKIP_VALUES
        rf.valid_dtcs_codes = DTCS_CODES
        rf.valid_bands = [self._range]
        rf.memory_bounds = (1, self._upper)
        return rf

    def sync_in(self):
        """Download from radio"""
        self._mmap = do_download(self)
        self.process_mmap()

    def sync_out(self):
        """Upload to radio"""
        # Get the data ready for upload
        try:
            self._prep_data()
        except:
            raise errors.RadioError("Error processing the radio data")

        # do the upload
        try:
            do_upload(self)
        except:
            raise errors.RadioError("Error uploading data to radio")

    def set_variant(self):
        """Select and set the correct variables for the class acording
        to the correct variant of the radio"""
        rid = get_rid(self._mmap)

        # indentify the radio variant and set the enviroment to it's values
        try:
            self._upper, low, high, self._kind = self.VARIANTS[rid]
            self._range = [low * 1000000, high * 1000000]

            # put the VARIANT in the class, clean the model / CHs / Type
            # in the same layout as the KPG program
            self._VARIANT = self.MODEL + " [" + str(self._upper) + "CH]: "
            self._VARIANT += self._kind + ", "
            self._VARIANT += str(self._range[0]/1000000) + "-"
            self._VARIANT += str(self._range[1]/1000000) + " Mhz"

        except KeyError:
            LOG.debug("Wrong Kenwood radio, ID or unknown variant")
            LOG.debug(util.hexprint(rid))
            raise errors.RadioError(
                "Wrong Kenwood radio, ID or unknown variant, see LOG output.")

    def _prep_data(self):
        """Prepare the areas in the memmap to do a consistend write
        it has to make an update on the x200 flag data"""
        achs = 0

        for i in range(0, self._upper):
            if self.get_active(i) is True:
                achs += 1

        # The x0200 area has the settings for the DTMF/2-Tone per channel,
        # as by default any of this radios has the DTMF IC installed;
        # we clean this areas
        fldata = "\x00\xf0\xff\xff\xff" * achs + \
            "\xff" * (5 * (self._upper - achs))
        self._fill(0x0200, fldata)

    def _fill(self, offset, data):
        """Fill an specified area of the memmap with the passed data"""
        for addr in range(0, len(data)):
            self._mmap[offset + addr] = data[addr]

    def process_mmap(self):
        """Process the mem map into the mem object"""
        self._memobj = bitwise.parse(MEM_FORMAT, self._mmap)
        # to set the vars on the class to the correct ones
        self.set_variant()

    def get_raw_memory(self, number):
        return repr(self._memobj.memory[number])

    def decode_tone(self, val):
        """Parse the tone data to decode from mem, it returns:
        Mode (''|DTCS|Tone), Value (None|###), Polarity (None,N,R)"""
        if val.get_raw() == "\xFF\xFF":
            return '', None, None

        val = int(val)
        if val >= 12000:
            a = val - 12000
            return 'DTCS', a, 'R'
        elif val >= 8000:
            a = val - 8000
            return 'DTCS', a, 'N'
        else:
            a = val / 10.0
            return 'Tone', a, None

    def encode_tone(self, memval, mode, value, pol):
        """Parse the tone data to encode from UI to mem"""
        if mode == '':
            memval[0].set_raw(0xFF)
            memval[1].set_raw(0xFF)
        elif mode == 'Tone':
            memval.set_value(int(value * 10))
        elif mode == 'DTCS':
            flag = 0x80 if pol == 'N' else 0xC0
            memval.set_value(value)
            memval[1].set_bits(flag)
        else:
            raise Exception("Internal error: invalid mode `%s'" % mode)

    def get_scan(self, chan):
        """Get the channel scan status from the 4 bytes array on the eeprom
        then from the bits on the byte, return '' or 'S' as needed"""
        result = "S"
        byte = int(chan/8)
        bit = chan % 8
        res = self._memobj.settings.scan[byte] & (pow(2, bit))
        if res > 0:
            result = ""

        return result

    def set_scan(self, chan, value):
        """Set the channel scan status from UI to the mem_map"""
        byte = int(chan/8)
        bit = chan % 8

        # get the actual value to see if I need to change anything
        actual = self.get_scan(chan)
        if actual != value:
            # I have to flip the value
            rbyte = self._memobj.settings.scan[byte]
            rbyte = rbyte ^ pow(2, bit)
            self._memobj.settings.scan[byte] = rbyte

    def get_active(self, chan):
        """Get the channel active status from the 4 bytes array on the eeprom
        then from the bits on the byte, return True/False"""
        byte = int(chan/8)
        bit = chan % 8
        res = self._memobj.settings.active[byte] & (pow(2, bit))
        return bool(res)

    def set_active(self, chan, value=True):
        """Set the channel active status from UI to the mem_map"""
        byte = int(chan/8)
        bit = chan % 8

        # get the actual value to see if I need to change anything
        actual = self.get_active(chan)
        if actual != bool(value):
            # I have to flip the value
            rbyte = self._memobj.settings.active[byte]
            rbyte = rbyte ^ pow(2, bit)
            self._memobj.settings.active[byte] = rbyte

    def get_memory(self, number):
        """Get the mem representation from the radio image"""
        _mem = self._memobj.memory[number - 1]
        _tone = self._memobj.tone[number - 1]
        _ch = self._memobj.ch_settings[number - 1]

        # Create a high-level memory object to return to the UI
        mem = chirp_common.Memory()

        # Memory number
        mem.number = number

        if _mem.get_raw()[0] == "\xFF" or not self.get_active(number - 1):
            mem.empty = True
            # but is not enough, you have to crear the memory in the mmap
            # to get it ready for the sync_out process
            _mem.set_raw("\xFF" * 8)
            return mem

        # Freq and offset
        mem.freq = int(_mem.rxfreq) * 10
        # tx freq can be blank
        if _mem.get_raw()[4] == "\xFF":
            # TX freq not set
            mem.offset = 0
            mem.duplex = "off"
        else:
            # TX feq set
            offset = (int(_mem.txfreq) * 10) - mem.freq
            if offset < 0:
                mem.offset = abs(offset)
                mem.duplex = "-"
            elif offset > 0:
                mem.offset = offset
                mem.duplex = "+"
            else:
                mem.offset = 0

        # power
        mem.power = POWER_LEVELS[_ch.power]

        # wide/marrow
        mem.mode = MODES[_ch.wide]

        # skip
        mem.skip = self.get_scan(number - 1)

        # tone data
        rxtone = txtone = None
        txtone = self.decode_tone(_tone.tx_tone)
        rxtone = self.decode_tone(_tone.rx_tone)
        chirp_common.split_tone_decode(mem, txtone, rxtone)

        # Extra
        # bank and number in the channel
        mem.extra = RadioSettingGroup("extra", "Extra")

        bl = RadioSetting("busy_lock", "Busy Channel lock",
                          RadioSettingValueBoolean(
                              not bool(_ch.busy_lock)))
        mem.extra.append(bl)

        return mem

    def set_memory(self, mem):
        """Set the memory data in the eeprom img from the UI
        not ready yet, so it will return as is"""

        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[mem.number - 1]
        _tone = self._memobj.tone[mem.number - 1]
        _ch = self._memobj.ch_settings[mem.number - 1]

        # Empty memory
        if mem.empty:
            _mem.set_raw("\xFF" * 8)
            # empty the active bit
            self.set_active(mem.number - 1, False)
            return

        # freq rx
        _mem.rxfreq = mem.freq / 10

        # freq tx
        if mem.duplex == "+":
            _mem.txfreq = (mem.freq + mem.offset) / 10
        elif mem.duplex == "-":
            _mem.txfreq = (mem.freq - mem.offset) / 10
        elif mem.duplex == "off":
            for i in range(0, 4):
                _mem.txfreq[i].set_raw("\xFF")
        else:
            _mem.txfreq = mem.freq / 10

        # tone data
        ((txmode, txtone, txpol), (rxmode, rxtone, rxpol)) = \
            chirp_common.split_tone_encode(mem)
        self.encode_tone(_tone.tx_tone, txmode, txtone, txpol)
        self.encode_tone(_tone.rx_tone, rxmode, rxtone, rxpol)

        # power, default power is low
        if mem.power is None:
            mem.power = POWER_LEVELS[0]

        _ch.power = POWER_LEVELS.index(mem.power)

        # wide/marrow
        _ch.wide = MODES.index(mem.mode)

        # skip
        self.set_scan(mem.number - 1, mem.skip)

        # extra settings
        for setting in mem.extra:
            setattr(_mem, setting.get_name(), setting.value)

        # set the mem a active in the _memmap
        self.set_active(mem.number - 1)

        return mem

    @classmethod
    def match_model(cls, filedata, filename):
        match_size = False
        match_model = False

        # testing the file data size
        if len(filedata) == MEM_SIZE:
            match_size = True

        # testing the firmware model fingerprint
        match_model = model_match(cls, filedata)

        if match_size and match_model:
            return True
        else:
            return False

    def get_settings(self):
        """Translate the bit in the mem_struct into settings in the UI"""
        sett = self._memobj.settings

        # basic features of the radio
        basic = RadioSettingGroup("basic", "Basic Settings")
        # buttons
        fkeys = RadioSettingGroup("keys", "Front keys config")

        top = RadioSettings(basic, fkeys)

        # Basic
        val = RadioSettingValueString(0, 35, self.VARIANT)
        val.set_mutable(False)
        mod = RadioSetting("not.mod", "Radio version", val)
        basic.append(mod)

        tot = RadioSetting("settings.tot", "Time Out Timer (TOT)",
                           RadioSettingValueList(TOT, TOT[int(sett.tot)]))
        basic.append(tot)

        minvol = RadioSetting("settings.min_vol", "Minimum volume",
                              RadioSettingValueList(VOL,
                                                    VOL[int(sett.min_vol)]))
        basic.append(minvol)

        ptone = RadioSetting("settings.poweron_tone", "Power On tone",
                             RadioSettingValueBoolean(
                                 bool(sett.poweron_tone)))
        basic.append(ptone)

        sprog = RadioSetting("settings.dealer_tuning", "Dealer Tuning",
                             RadioSettingValueBoolean(
                                 bool(sett.dealer_tuning)))
        basic.append(sprog)

        clone = RadioSetting("settings.clone", "Allow clone",
                             RadioSettingValueBoolean(
                                 bool(sett.clone)))
        basic.append(clone)

        # front keys
        mon = RadioSetting("settings.kMON", "MON",
                           RadioSettingValueList(KEYS.values(),
                           KEYS.values()[KEYS.keys().index(
                               int(sett.kMON))]))
        fkeys.append(mon)

        a = RadioSetting("settings.kA", "A",
                         RadioSettingValueList(KEYS.values(),
                         KEYS.values()[KEYS.keys().index(
                             int(sett.kA))]))
        fkeys.append(a)

        scn = RadioSetting("settings.kSCN", "SCN",
                           RadioSettingValueList(KEYS.values(),
                           KEYS.values()[KEYS.keys().index(
                               int(sett.kSCN))]))
        fkeys.append(scn)

        da = RadioSetting("settings.kDA", "D/A",
                          RadioSettingValueList(KEYS.values(),
                          KEYS.values()[KEYS.keys().index(
                              int(sett.kDA))]))
        fkeys.append(da)

        return top

    def set_settings(self, settings):
        """Translate the settings in the UI into bit in the mem_struct
        I don't understand well the method used in many drivers
        so, I used mine, ugly but works ok"""

        mobj = self._memobj

        for element in settings:
            if not isinstance(element, RadioSetting):
                self.set_settings(element)
                continue

            # Let's roll the ball
            if "." in element.get_name():
                inter, setting = element.get_name().split(".")
                # you must ignore the settings with "not"
                # this are READ ONLY attributes
                if inter == "not":
                    continue

                obj = getattr(mobj, inter)
                value = element.value

                # case keys, with special config
                if setting[0] == "k":
                    value = KEYS.keys()[KEYS.values().index(str(value))]

                # integers case + special case
                if setting in ["tot", "min_vol"]:
                    # catching the "off" values as zero
                    try:
                        value = int(value)
                    except:
                        value = 0

                # Bool types + inverted
                if setting in ["poweron_tone", "dealer_tuning", "clone"]:
                    value = bool(value)

            # Apply al configs done
            # DEBUG
            #print("%s: %s" % (setting, value))
            setattr(obj, setting, value)


# This are the oldest family 60 models (Black keys), just mobiles support here

@directory.register
class TK760_Radio(Kenwood_M60_Radio):
    """Kenwood TK-760 Radios"""
    MODEL = "TK-760"
    TYPE = "M0760"
    VARIANTS = {
        "M0760\x01\x00\x00": (32, 136, 156, "K2"),
        "M0760\x00\x00\x00": (32, 148, 174, "K")
        }


@directory.register
class TK762_Radio(Kenwood_M60_Radio):
    """Kenwood TK-762 Radios"""
    MODEL = "TK-762"
    TYPE = "M0762"
    VARIANTS = {
        "M0762\x01\x00\x00": (2, 136, 156, "K2"),
        "M0762\x00\x00\x00": (2, 148, 174, "K")
        }


@directory.register
class TK768_Radio(Kenwood_M60_Radio):
    """Kenwood TK-768 Radios"""
    MODEL = "TK-768"
    TYPE = "M0768"
    VARIANTS = {
        "M0768\x21\x00\x00": (32, 136, 156, "K2"),
        "M0768\x20\x00\x00": (32, 148, 174, "K")
        }


@directory.register
class TK860_Radio(Kenwood_M60_Radio):
    """Kenwood TK-860 Radios"""
    MODEL = "TK-860"
    TYPE = "M0860"
    VARIANTS = {
        "M0860\x05\x00\x00": (32, 406, 430, "F4"),
        "M0860\x04\x00\x00": (32, 488, 512, "F3"),
        "M0860\x03\x00\x00": (32, 470, 496, "F2"),
        "M0860\x02\x00\x00": (32, 450, 476, "F1")
        }


@directory.register
class TK862_Radio(Kenwood_M60_Radio):
    """Kenwood TK-862 Radios"""
    MODEL = "TK-862"
    TYPE = "M0862"
    VARIANTS = {
        "M0862\x05\x00\x00": (2, 406, 430, "F4"),
        "M0862\x04\x00\x00": (2, 488, 512, "F3"),
        "M0862\x03\x00\x00": (2, 470, 496, "F2"),
        "M0862\x02\x00\x00": (2, 450, 476, "F1")
        }


@directory.register
class TK868_Radio(Kenwood_M60_Radio):
    """Kenwood TK-868 Radios"""
    MODEL = "TK-868"
    TYPE = "M0868"
    VARIANTS = {
        "M0868\x25\x00\x00": (32, 406, 430, "F4"),
        "M0868\x24\x00\x00": (32, 488, 512, "F3"),
        "M0868\x23\x00\x00": (32, 470, 496, "F2"),
        "M0868\x22\x00\x00": (32, 450, 476, "F1")
        }
