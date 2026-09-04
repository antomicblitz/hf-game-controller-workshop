# 3. Customize and print the case

The approved default case is 130 x 90 x 65 mm with four directional buttons on
the left and action A/B on the right. The editor permits bounded shape changes
and validated button moves while protecting the electronics, USB opening,
shell, and snap-fit regions.

## Start the editor

```console
make doctor
make editor
```

Open the `http://127.0.0.1:...` URL printed in the terminal. The editor is
available only on your computer.

1. Use **2D Layout** to drag a permitted button.
2. Use **3D Review** to orbit and inspect the assembled controller.
3. Describe a case-shape change, then press **Send**.
4. Compare **Before** and **After**.
5. Download `case-top.stl` and `case-bottom.stl`.

Invalid or unprintable changes are rejected. **Reset design** returns to the
approved rounded case. Press **Ctrl+C** in the terminal to stop the editor.

If customization is unavailable, use the known-good files in
`controller/default-stl/`.

## PrusaSlicer and SD card

PrusaSlicer is already installed for the course; this repository does not
install or control it.

1. Open both STLs in PrusaSlicer.
2. Select the preset for the assigned printer, a **0.4 mm nozzle**, and **PLA**.
3. Keep the supplied flat orientation. Confirm each half touches the build
   plate correctly.
4. Use **0.28 mm** layers, **3 perimeters**, **20% gyroid infill**, and
   **supports off**, unless Antonio gives your team a different preset.
5. Slice and inspect the complete layer preview before exporting.
6. Export G-code to the SD card, safely eject it, insert it in the assigned
   printer, and start the file from the printer controls.

Do not send files directly to a printer from this repository.
