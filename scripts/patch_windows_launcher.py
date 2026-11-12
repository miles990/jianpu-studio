"""Opt Audiveris' launcher into Windows 10 1903+ UTF-8 process paths.

Preserve the upstream manifest, including its UAC settings. This changes only
the PE application manifest; the patch is distributed with corresponding source.
"""
import argparse
import xml.dom.minidom
from pathlib import Path


def patch_launcher(executable: Path):
    from PyInstaller.utils.win32.winmanifest import (
        read_manifest_from_executable,
        write_manifest_to_executable,
    )

    resource = read_manifest_from_executable(str(executable))
    # PyInstaller's fallback for a non-neutral resource language returns
    # (language_id, bytes), while LANG_NEUTRAL returns bytes directly.
    if isinstance(resource, tuple):
        resource = resource[1]
    document = xml.dom.minidom.parseString(resource.rstrip(b"\0"))
    assembly_ns = "urn:schemas-microsoft-com:asm.v3"
    setting_ns = "http://schemas.microsoft.com/SMI/2019/WindowsSettings"
    applications = document.getElementsByTagNameNS(assembly_ns, "application")
    if applications:
        application = applications[0]
    else:
        application = document.createElementNS(assembly_ns, "application")
        application.setAttribute("xmlns", assembly_ns)
        document.documentElement.appendChild(application)
    settings = application.getElementsByTagNameNS(assembly_ns, "windowsSettings")
    if settings:
        settings = settings[0]
    else:
        settings = document.createElementNS(assembly_ns, "windowsSettings")
        application.appendChild(settings)
    existing = settings.getElementsByTagNameNS(setting_ns, "activeCodePage")
    if existing:
        page = existing[0]
        for node in list(page.childNodes):
            page.removeChild(node)
    else:
        page = document.createElementNS(setting_ns, "activeCodePage")
        page.setAttribute("xmlns", setting_ns)
        settings.appendChild(page)
    page.appendChild(document.createTextNode("UTF-8"))
    write_manifest_to_executable(str(executable), document.toxml(encoding="utf-8"))
    print(f"Enabled UTF-8 paths in {executable.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    patch_launcher(parser.parse_args().executable)
