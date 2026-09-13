<table width="100%">
<thead>
<tr><th align="left">🚧 UNDER CONSTRUCTION</th></tr>
</thead>
<tbody>
<tr><td>

The project compiles and runs, but it is still early in development. Many features are missing, bugs are common, and it is not stable yet. Feel free to explore if you are curious, but if you need something reliable, please check back later once the project has matured into a stable release.

</td></tr>
</tbody>
</table>

 <table width="100%">
 <thead>
 <tr><th align="left">📢❗🚨 SECURITY CONCERN</th></tr>
 </thead>
 <tbody>
 <tr><td>

Please read this part. Do not skip it.

The security of Azzio has not been thoroughly reviewed yet. There are no *SECURITY.md* or *ISSUES.md* files. There is also no process in place for onboarding developers, tracking issues, accepting pull requests, or reporting vulnerabilities.

The project recently went through a major overhaul and was renamed to Azzio. During that work, coding LLMs were used to write a lot of the code. That generated code has only been skimmed for basic security and style. It has not been carefully reviewed.

So if you plan to use this project in any way, please read the code first. Do not run anything until you have checked it yourself.

 </td></tr>
 </tbody>
 </table>


<p align="center">
  <img src="assets/logos/azzio_title_627×230.png" alt="Azzio">
</p>

## Documentation

**Technical Specifications**

- [documentations/SPECIFICATIONS_GENERAL.md](documentations/SPECIFICATIONS_GENERAL.md)

**Interactive Packages Graph**

- [https://azzio.baselinux.net/documentations/SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html](https://azzio.baselinux.net/documentations/SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html)
- [https://michaelilgiaev.github.io/azzio/documentations/SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html](https://michaelilgiaev.github.io/azzio/documentations/SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html)
- [documentations/SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html](documentations/SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html)

## Install

1. **Download the ISO (or [compile the ISO yourself](#compile))**  

     <table width="100%">
     <thead>
     <tr><th align="left">🚧 UNDER CONSTRUCTION</th></tr>
     </thead>
     <tbody>
     <tr><td>

     ~https://azzio.baselinux.net/download/azzio-headed-2026.09.08-x86_64.iso~

     ~https://azzio.baselinux.net/download/azzio-headless-2026.09.08-x86_64.iso~

     </td></tr>
     </tbody>
     </table>

2. **Create a Bootable USB**  

     <table width="100%">
     <thead>
     <tr><th align="left">📢❗🚨 PLEASE BE CAREFUL</th></tr>
     </thead>
     <tbody>
     <tr><td>

     This will erase everything on the USB!

     </td></tr>
     </tbody>
     </table>

   Use one of the following tools to write the ISO to a USB drive:
   - **[balenaEtcher](https://etcher.balena.io/)** (Windows/macOS/Linux)
   - **[Rufus](https://rufus.ie/en/)** (Windows only)
   - `dd` command (Linux/macOS):

     Replace `<DEVICE>` with the USB device and replace `<ISO>` with the ISO file.

     ```bash
     sudo dd if=<ISO> of=/dev/<DEVICE> bs=4M oflag=direct status=progress
     ```

     <table width="100%">
    <thead>
    <tr><th align="left">ℹ️ SIDE NOTE</th></tr>
    </thead>
    <tbody>
    <tr><td>

     Run `lsblk` to find `<DEVICE>`:

     ```bash
     lsblk -o NAME,SIZE,TYPE,TRAN,FSTYPE,MOUNTPOINTS
     ```

     The USB is the whole-disk entry (such as `sdb`) whose size matches the stick,
     so use `/dev/sdb`, not a partition like `/dev/sdb1`.

     </td></tr>
     </tbody>
     </table>

3. **Boot from USB**  
   Reboot your machine and use your BIOS/UEFI boot menu to boot from the USB drive.

4. **Live Session and Installation**  

   The ISO boots into a live session and automatically launches the Azzio
   installer, which is powered by Calamares.

   From the live session you can:
   - Install Azzio.
   - Perform machine rescue tasks.
   - Do general work.

   <table width="100%">
   <thead>
   <tr><th align="left">ℹ️ SIDE NOTE</th></tr>
   </thead>
   <tbody>
   <tr><td>

   Some considerations when using the live session:
   - Reserves 4 GB of RAM.
   - Runs entirely from RAM, nothing gets saved after reboot.
   - Uses a generic open-source graphics driver.

   </td></tr>
   </tbody>
   </table>

## Compile

You can clone this repository and compile the ISO yourself. The first compile needs an internet connection to download every package that goes into the ISO. After that everything is cached and recompiles run fully offline. The compiler requires mkarchiso along with the Arch core, extra, and multilib repositories, so it has to run inside Arch Linux itself. It also installs packages and modifies files directly on the host machine. For these reasons it is recommended to compile the ISO using Docker.

1. **Install Docker and Git.**

   <b>Linux</b>

   - Install both with your package manager:
     - Arch-based: `sudo pacman -S --needed docker git`
     - Debian/Ubuntu: `sudo apt update && sudo apt install docker.io git`
     - Fedora: `sudo dnf install docker git`
   - Start Docker: `sudo systemctl enable --now docker`

   <b>macOS</b>

   - Install [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) and launch it (wait until the whale icon says Docker is running).
   - Git ships with the Xcode command line tools: `xcode-select --install`

   <b>Windows</b>

   - In an Administrator PowerShell, install WSL2: `wsl --install` (you may be prompted to reboot).
   - Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) and enable **"Use the WSL 2 based engine"** in its settings.
   - Open your WSL distro (e.g. Ubuntu) and install Git: `sudo apt update && sudo apt install git`

2. **Clone the repository and enter it**
   ```
   git clone https://github.com/michaelilgiaev/azzio.git && cd azzio
   ```

3. **Compile the Docker image** (creates the Arch compile environment)
   ```
   sudo docker build -t azzio .
   ```

4. **Compile the ISO.** The finished ISO goes to `output/`, downloaded packages
   are cached in `cache/`, and compile logs go to `logs/`.

   **Default compile** (recommended). Compiles only what's necessary. Everything else is downloaded as trusted, verified binaries.
   ```
   sudo docker run --rm -it --init --privileged \
     -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
     -v "$PWD/cache:/build/cache" \
     -v "$PWD/output:/build/output" \
     -v "$PWD/logs:/build/logs" \
     azzio
   ```

   **Full compile.** Compiles everything from source, which takes hours.

   ```
   sudo docker run --rm -it --init --privileged \
     -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
     -v "$PWD/cache:/build/cache" \
     -v "$PWD/output:/build/output" \
     -v "$PWD/logs:/build/logs" \
     azzio --full-compile
   ```

   <table width="100%">
   <thead>
   <tr><th align="left">ℹ️ SIDE NOTE</th></tr>
   </thead>
   <tbody>
   <tr><td>

   The following "--estimate" flags don't compile or download anything, they just measure your machine and connection and print how long a compile would take, then exit (no `sudo`, no privileged mounts needed). There are six, picking the compile tier (default vs `--full-compile`) and what to estimate:

   | Flag | Tier | Estimates |
   | --- | --- | --- |
   | `--estimate` | default | compile time **and** download time |
   | `--estimate-only-compute` | default | compile time only |
   | `--estimate-only-network` | default | download time only |
   | `--estimate-full-compile` | full | compile time **and** download time |
   | `--estimate-full-compile-only-compute` | full | compile time only |
   | `--estimate-full-compile-only-network` | full | download time only |

   The compile estimate reads your CPU cores and RAM; the network estimate runs a short bandwidth test against an Arch mirror and divides the tier's download size by your measured speed. Example (estimate a full compile, compute + network):

   ```
   sudo docker run --rm -it azzio --estimate
   ```

   </td></tr>
   </tbody>
   </table>

5. **Get the ISO.** It's in the `output/` folder. On **Windows (WSL)** that folder
   opens in File Explorer at `\\wsl$\<distro>\home\<your-username>\azzio\output`.

- **Wipe the cache** to force a fresh, fully-online recompile. Run `clear.sh`, which with no flags deletes the `cache/`, `output/`, and `logs/` directories (and sweeps every `__pycache__`):

  ```
  bash clear.sh
  ```

  <table width="100%">
  <thead>
  <tr><th align="left">ℹ️ SIDE NOTE</th></tr>
  </thead>
  <tbody>
  <tr><td>

  For anyone working with this repository, the following optional flags are a safe way to clear compiled directories. Combine flags to clear several, omitting flags will clear all compiled diretories.

  | Flag | Clears |
  | --- | --- |
  | `--output`, `-o` | only `output/` |
  | `--logs`, `-l` | only `logs/` |
  | `--cache`, `-c` | only `cache/` (also sweeps every `__pycache__`) |
  | `--help`, `-h` | prints usage, then exits |

  Example (wipe the built ISO and logs, but keep the cache):

  ```
  bash clear.sh --output --logs
  ```

  If the compile was stopped mid-process, the ownership handback may not have run, so some files in `cache/` can be left root-owned. In that case wipe it with `sudo` (the same flags apply):

  ```
  sudo bash clear.sh
  ```
