# PC Stitch Designer

## Application Overview

This application is designed to enable users to create, edit, and transfer custom stitch patterns to compatible sewing machines (PFAFF Creative 7570, 7550, 1475 CD).

It serves as a modern replacement for "PFAFF PC Designer 2.2", a legacy software originally developed for Windows 3.11. Due to its age, the original software is no longer compatible with recent 64-bit Windows systems. This project aims to allow owners of old sewing machines continue using them with modern PCs.

## Main window of the application

<img width="900" alt="main_window" src="https://github.com/user-attachments/assets/7da7d1e2-0498-4ed1-9e99-2b469eed21a3" />

## Available Features

- Creating custom stitch patterns ("P-Designs", both 9mm and MAXI-stiches)
- Import and export from/to stitch file formats:
  - *.PCD (9 mm stitches)
  - *.PCQ (MAXI stitches)
- Transfer stitch patterns to and from supported sewing machines
  - PFAFF Creative 7570 (both internal memory and [memory cards](https://github.com/arthendev/pcstitchdesigner/wiki/Memory-Card) are supported)
  - PFAFF Creative 7550
  - PFAFF Creative 1475 CD (not tested yet or real hardware)
- Compatibility with modern 64-bit operating systems
- App interface is available in English and German

## Hardware requirements

- Compatible sewing machine
  - PFAFF Creative 7570, 7550 or 1475 CD
- Interface cable
  - [compatible USB-PFAFF cable](https://github.com/arthendev/pcstitchdesigner/wiki/Machine-Communication)
  - original COM cable should eventually work but was not tested yet

## Download

You can get a Windows executable or source files under the [Releases](https://github.com/arthendev/pcstitchdesigner/releases) section.

On Windows: just unpack and run the executable.

On Linux or macOS: download and unpack the sources and run pc_designer.py with your local Python instance. Check [wiki/Installation](https://github.com/arthendev/pcstitchdesigner/wiki/Installation) for details.

## User Manual

[Online Documentation](https://github.com/arthendev/pcstitchdesigner/wiki) is available

## Planned Features

The following features are planned for future releases:

- Creation and transfer of stitch sequences ("M-Designs")
- Embroidery Design Support
  - Loading embroidery design files (already done)
  - Transferring embroidery designs to and from the sewing machine
  - Support for PFAFF Creative 7560

## Donations

This project was created entirely in my free time. It started when I was looking for a high-quality sewing machine and, by chance, came across a PFAFF Creative series from 1990s. Being immediately impressed by the craftsmanship and engineering of this classic machine, it was sad to see the original software tools are outdated and not usable on recent computers anymore. I decided to build a modern, user-friendly alternative, that allows owners of these remarkable machines to continue using them with their own stitch designs.

If you find this project useful and would like to say "thank you" for the work already done, you are very welcome to make a small donation.

[![](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=ALB975LFDA7AE)

## Disclaimer

This software is a hobby project, not related to any manufacturer of consumer or industrial sewing equipment. It comes without any warranties. Always backup your important data!

Thank you!
