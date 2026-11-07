# PC Stitch Designer

## Application Overview

This application is designed to enable users to create, edit, and transfer custom stitch patterns to compatible sewing machines (Creative 7570, 7550, 1475 CD from Pfaff).

It serves as a modern alternative to "PC Designer 2.2", a legacy software originally developed for Windows 3.11. Due to its age, the original software is no longer compatible with recent 64-bit Windows systems. This project aims to allow owners of old sewing machines continue using them with modern PCs.

## Main window of the application

<img width="1202" height="732" alt="main_window_overview_2" src="https://github.com/user-attachments/assets/3c2034ea-6278-433e-9e9e-7b45cebc63d3" />

## Embroidery

Since version 0.9.3 also embroidery designs can be opened, edited and transferred to compatible sewing machines (Creative 7570, 7560 and 7562).

<img height="250" src="https://github.com/user-attachments/assets/7be96877-96a0-450d-a0ca-7c7d42eba53a" />
<img height="250" src="https://github.com/user-attachments/assets/4091c544-1392-4ad3-8471-de8e4544c61d" />

## Available Features

- Creating custom stitch patterns ("P-Designs", both 9mm and MAXI-stitches)
- Import and export from/to stitch file formats:
  - *.PCD and *.9MM (9 mm stitches)
  - *.PCQ and *.MAX (MAXI stitches)
  - *.PCS (embroidery designs)
- Import of third-party embroidery formats (read-only):
  -  *.DST, *.JEF, *.PEC, *.PES, *.HUS, *.VIP and *.VP3
- Transfer stitch patterns to and from supported sewing machines
  - Pfaff Creative 7570 (both internal memory and [memory cards](https://github.com/arthendev/pcstitchdesigner/wiki/Memory-Card) are supported)
  - Pfaff Creative 7560/7562 (memory cards only; embroidery designs can be stored on a card)
  - Pfaff Creative 7550
  - Pfaff Creative 1475 CD
- Compatibility with modern 64-bit operating systems
- App interface is available in English, Czech, Danish, Dutch, French, German,
  Italian, Polish, Russian, Slovak and Swedish

## Hardware requirements

- Compatible sewing machine
  - Creative 7570, 7560, 7550 or 1475 CD
- Interface cable
  - [compatible USB cable](https://github.com/arthendev/pcstitchdesigner/wiki/Machine-Communication)
  - original COM cable should eventually work but was not tested yet

## Download

You can get a Windows executable or source files under the [Releases](https://github.com/arthendev/pcstitchdesigner/releases) section.

On Windows: just unpack and run the executable.

On Linux or macOS: download and unpack the sources and run pc_designer.py with your local Python instance. Check [wiki/Installation](https://github.com/arthendev/pcstitchdesigner/wiki/Installation) for details.

## User Manual

[Online Documentation](https://github.com/arthendev/pcstitchdesigner/wiki) is available

## Donations

This project was created entirely in my free time. It started when I was looking for a high-quality sewing machine and, by chance, came across a Pfaff Creative series from 1990s. Being immediately impressed by the craftsmanship and engineering of this classic machine, it was sad to see the original software tools are outdated and not usable on recent computers anymore. I decided to build a modern, user-friendly alternative, that allows owners of these remarkable machines to continue using them with their own stitch designs.

If you find this project useful and would like to say "thank you" for the work already done, you are very welcome to make a small donation.

[![](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=ALB975LFDA7AE)

## Disclaimer

This software is a hobby project, not related to any manufacturer of consumer or industrial sewing equipment. It comes without any warranties. Always backup your important data!

Thank you!
