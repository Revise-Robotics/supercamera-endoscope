# protocol notes

what we know about how these cameras talk over usb, and how sure we are about each part.
"tested" means seen on a real camera on our desk. everything else comes from reading the
usee plus android app (static analysis only) or from other people's projects.

## the cameras we have seen

all of them are `2ce3:3828`, "Geek szitman supercamera". vendor interfaces are class `0xff`,
subclass `0xf0`. protocol 0 is the iAP interface, protocol 1 is the data interface.

| | bcdDevice | interfaces | iAP endpoints | data endpoints | data interface string | frames | status |
|-|-----------|-----------|---------------|----------------|-----------------------|--------|--------|
| A | `0x0100` | 0 (iAP), 1 (data, alt 1) | `0x82` in, `0x02` out | `0x81` in, `0x01` out | `com.useeplus.protocol` | jpeg 640x480 | tested (vividia fc-5550i) |
| B | `0x0111` | 0 (iAP), 1 (data, alt 1) | `0x81` in, `0x01` out | `0x82` in, `0x02` out | `com.ihighsky.dptl` | raw yuyv 320x240 | tested |
| C | not known | 0 only | none | `0x82` in, `0x02` out | not known | raw yuyv | not tested by us, see issue #1 |

A and B look almost the same from the outside (same id, two interfaces), but the endpoint
numbers are swapped and they speak different protocols.

## how the driver picks

- interface 1 exists and bcdDevice is not `0x0111`: jpeg
- interface 1 exists and bcdDevice is `0x0111`: raw, on interface 1
- only interface 0: raw, on interface 0

the usee plus app does it a bit differently (`OtgCameraApi.get_utype`): a protocol 0 plus a
protocol 1 interface is "JPEG", only protocol 1 is "YUV", anything else is "OTG". it also has
a check for bcdDevice `0x0111`.

## jpeg protocol (camera A)

1. claim interfaces 0 and 1, drain whatever is waiting on the iAP in endpoint
2. set interface 1 to alt 1
3. write `ff 55 ff 55 ee 10` to the iAP out endpoint
4. write `bb aa 05 00 00` to the data out endpoint
5. bulk read from the data in endpoint. each packet has a 12 byte header starting `aa bb`,
   the rest is jpeg data. a frame runs from `ff d8` to `ff d9`
6. the first frame after connect is partial, skip it

the app does one more step that we do not: after `bb aa 05 00 00` it reads a reply, and if
byte 2 of the reply is 5 it writes `bb aa 06 00 00` until a reply has byte 2 equal to 6.
camera A streams without this.

## raw protocol (cameras B and C)

all three requests are control transfers with wValue 5, wIndex 0.

| what | bmRequestType | bRequest | data |
|------|---------------|----------|------|
| getinfo | `0xa0` (in) | 0 | read 512 bytes |
| camera up | `0x20` (out) | 1 | 64 zero bytes |
| camera down | `0x20` (out) | 2 | none |

open: claim the data interface, set its alt setting if the endpoints live on alt 1, getinfo,
camera up, then bulk read from the in endpoint. release: camera down.

### getinfo reply

camera B replies with 480 bytes and no prefix. the app also accepts a form with a 6 byte
prefix (`cc bb`, a type byte that is 1 for product info, 3 more bytes) in front of the same
payload. we have not seen a camera send the prefixed form.

| offset | size | field | camera B |
|--------|------|-------|----------|
| `0x00` | 1 | config version | |
| `0x01` | 16 | vendor | `i4season` |
| `0x11` | 16 | product | `su4p-002` |
| `0x21` | 8 | version | `5.0.13` |
| `0x29` | 1 | format | 2 |
| `0x2a` | 4 | max payload, u32 le | 153600 |
| `0x2e` | 2 | width, u16 le | 320 |
| `0x30` | 2 | height, u16 le | 240 |
| `0x42` | 1 | capacity, only if config version >= 3 | |

max payload is width x height x 2 on camera B.

### stream

a frame is a 511 byte header followed by width x height x 2 bytes of packed yuyv
(y0 u y1 v). on camera B this order gives colours that match the room, checked by eye
on one scene, not against a colour chart. the header arrives as a short packet, which is how you find frame boundaries:

- a read whose length is a multiple of 512 is all pixel data
- a read whose length is 511 mod 512 ends with a header, and the bytes before it finish the
  previous frame
- anything else: throw away what you have and wait for the next header

on camera B with 16384 byte reads the sizes are 511 once, then 9 x 16384 + 6655 per frame.
its header starts `dd cc 01 00 58 02 00 00` (`0x00025800` is 153600) and is the same on
every frame.

other projects for camera C hardcode 320x240 and describe a 512 byte preamble on the first
block instead. with their read size that gives the same frames.

## what we do not know

- the rest of the 511 byte header, and what the format values mean
- camera B does not reply to the jpeg style open at all. unexplained
- camera B on our desk had the iAP magic written to it earlier in the same power cycle before
  the raw open worked. whether the raw open works straight after plugging in is not verified.
  right after a plug in its iAP in endpoint had `ff 55 02 00 ee 10` waiting
- camera C in issue #1 is a dual lens model and gave a garbled picture. the app has a camera
  switch command (`bb aa 0b ...`) and per camera resolution lists that the driver does not
  implement. whether that is related is not known
- the driver hardcodes camera A's endpoint numbers on the jpeg path, so a jpeg camera with
  swapped endpoints (`3301:2001` in kandotrun/supercamera) would not open

## where this comes from

- usee plus android app, com.i4season.useeplus 1.0.106 (jadx for the java, ghidra for
  `libOtgCameraLibUSB.so`). never installed or run
- [hbens/geek-szitman-supercamera](https://github.com/hbens/geek-szitman-supercamera) and
  [MAkcanca/useeplus-linux-driver](https://github.com/MAkcanca/useeplus-linux-driver) for the jpeg protocol
- [Bognabon/Endoscope_Viewer](https://github.com/Bognabon/Endoscope_Viewer),
  [KlumpRasmus/supercamera-yuyv-linux](https://github.com/KlumpRasmus/supercamera-yuyv-linux),
  [JimKnopfIoT/harbour-pipecam](https://github.com/JimKnopfIoT/harbour-pipecam) for the raw protocol on one interface
- https://coryk.ing/scope/ for a `com.ihighsky.dptl` borescope using the raw protocol
- [kandotrun/supercamera](https://github.com/kandotrun/supercamera) for the swapped endpoint jpeg camera
