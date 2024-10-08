from Scripts.datasets import os_data
from Scripts import acpi_guru
from Scripts import compatibility_checker
from Scripts import config_prodigy
from Scripts import gathering_files
from Scripts import kext_maestro
from Scripts import smbios
from Scripts import utils
import updater
import os
import sys
import re
import shutil
import traceback
import time

class OCPE:
    def __init__(self):
        self.u = utils.Utils("OpCore Simplify")
        self.o = gathering_files.gatheringFiles()
        self.ac = acpi_guru.ACPIGuru()
        self.c = compatibility_checker.CompatibilityChecker()
        self.co = config_prodigy.ConfigProdigy()
        self.k = kext_maestro.KextMaestro()
        self.s = smbios.SMBIOS()
        self.u = utils.Utils()
        self.result_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "Results")

    def gathering_files(self):
        self.u.head("Gathering Files")
        print("")
        print("Please wait for download OpenCore NO ACPI, kexts and macserial...")
        print("")

        self.o.get_bootloader_kexts_data()
        self.o.gathering_bootloader_kexts()

    def select_hardware_report(self):
        while True:
            self.u.head("Select hardware report")
            print("")
            print("To ensure the best results, please follow these instructions before generating the hardware report:")
            print("")
            print("  1. Install all available drivers if possible (skip this step when using Windows PE)")
            print("  2. Use the latest version of Hardware Sniffer")
            print("")
            print("Q. Quit")
            print("")
            user_input = self.u.request_input("Please drag and drop your hardware report here: (.JSON) ")
            if user_input.lower() == "q":
                self.u.exit_program()
            path = self.u.normalize_path(user_input)
            data = self.u.read_file(path)
            if not path or os.path.splitext(path)[1].lower() != ".json" or not isinstance(data, dict): 
                continue
            return path, data

    def select_macos_version(self, supported_macos_version):
        version_pattern = re.compile(r'^(\d+)(?:\.(\d+)(?:\.(\d+))?)?$')

        while True:
            self.u.head("Select macOS Version")
            print("")
            for darwin_version in range(int(supported_macos_version[0][:2]), int(supported_macos_version[-1][:2]) + 1):
                print("{}. {}".format(darwin_version, os_data.get_macos_name_by_darwin(str(darwin_version))))
            print("")
            print("Please enter the macOS version you want to select:")
            print("- To select a major version, enter the number (e.g., 19).")
            print("- To specify a full version, use the Darwin version format (e.g., 22.4.6).")
            print("- The version must be in the range from {} to {}.".format(supported_macos_version[0], supported_macos_version[-1]))
            print("")
            print("Q. Quit")
            print("")
            option = self.u.request_input("Select macOS version: ")
            if option.lower() == "q":
                self.u.exit_program()

            match = version_pattern.match(option)
            if match:
                target_version = "{}.{}.{}".format(match.group(1), match.group(2) if match.group(2) else 99, match.group(3) if match.group(3) else 99)
                
                if self.u.parse_darwin_version(supported_macos_version[0]) <= self.u.parse_darwin_version(target_version) <= self.u.parse_darwin_version(supported_macos_version[-1]):
                    return target_version

    def build_opencore_efi(self, hardware_report, unsupported_devices, smbios_model, macos_version):
        self.u.head("Building OpenCore EFI")
        print("")
        print("1. Copy EFI base to results folder...", end=" ")
        self.u.create_folder(self.result_dir, remove_content=True)

        if not os.path.exists(self.k.ock_files_dir):
            raise Exception("Directory '{}' does not exist.".format(self.k.ock_files_dir))
        
        source_efi_dir = os.path.join(self.k.ock_files_dir, "OpenCore")
        shutil.copytree(source_efi_dir, self.result_dir, dirs_exist_ok=True)
        print("Done")
        print("2. Generate config.plist...", end=" ")
        config_file = os.path.join(self.result_dir, "EFI", "OC", "config.plist")
        config_data = self.u.read_file(config_file)
        
        if not config_data:
            raise Exception("Error: The file {} does not exist.".format(config_file))
        
        self.co.genarate(hardware_report, unsupported_devices, smbios_model, macos_version, self.k.kexts, config_data)
        print("Done")
        print("3. Apply ACPI patches...", end=" ")
        self.ac.hardware_report = hardware_report
        self.ac.unsupported_devices = unsupported_devices
        self.ac.acpi_directory = os.path.join(self.result_dir, "EFI", "OC", "ACPI")
        self.ac.smbios_model = smbios_model
        self.ac.get_low_pin_count_bus_device()

        for patch in self.ac.patches:
            if patch.checked:
                if patch.name == "BATP":
                    patch.checked = getattr(self.ac, patch.function_name)()
                    continue

                acpi_load = getattr(self.ac, patch.function_name)()

                if not isinstance(acpi_load, dict):
                    continue

                config_data["ACPI"]["Add"].extend(acpi_load.get("Add", []))
                config_data["ACPI"]["Delete"].extend(acpi_load.get("Delete", []))
                config_data["ACPI"]["Patch"].extend(acpi_load.get("Patch", []))

        config_data["ACPI"]["Patch"] = self.ac.apply_acpi_patches(config_data["ACPI"]["Patch"])
        print("Done")
        print("4. Copy kexts and snapshot to config.plist...", end=" ")
        kexts_directory = os.path.join(self.result_dir, "EFI", "OC", "Kexts")
        self.k.install_kexts_to_efi(macos_version, kexts_directory)
        config_data["Kernel"]["Add"] = self.k.load_kexts(macos_version, kexts_directory)

        self.u.write_file(config_file, config_data)
        print("Done")
        print("5. Clean up unused drivers, resources, and tools...", end=" ")
        files_to_remove = []

        drivers_directory = os.path.join(self.result_dir, "EFI", "OC", "Drivers")
        driver_list = self.u.find_matching_paths(drivers_directory, extension_filter=".efi")
        driver_loaded = [kext.get("Path") for kext in config_data.get("UEFI").get("Drivers")]
        for driver_path, type in driver_list:
            if not driver_path in driver_loaded:
                files_to_remove.append(os.path.join(drivers_directory, driver_path))

        resources_audio_dir = os.path.join(self.result_dir, "EFI", "OC", "Resources", "Audio")
        if os.path.exists(resources_audio_dir):
            files_to_remove.append(resources_audio_dir)

        picker_variant = config_data.get("Misc", {}).get("Boot", {}).get("PickerVariant")
        if picker_variant in (None, "Auto"):
            picker_variant = "Acidanthera/GoldenGate" 
        if os.name == "nt":
            picker_variant = picker_variant.replace("/", "\\")

        resources_image_dir = os.path.join(self.result_dir, "EFI", "OC", "Resources", "Image")
        available_picker_variants = self.u.find_matching_paths(resources_image_dir, type_filter="dir")

        for variant_name, variant_type in available_picker_variants:
            variant_path = os.path.join(resources_image_dir, variant_name)
            if ".icns" in ", ".join(os.listdir(variant_path)):
                if picker_variant not in variant_name:
                    files_to_remove.append(variant_path)

        tools_directory = os.path.join(self.result_dir, "EFI", "OC", "Tools")
        tool_list = self.u.find_matching_paths(tools_directory, extension_filter=".efi")
        tool_loaded = [tool.get("Path") for tool in config_data.get("Misc").get("Tools")]
        for tool_path, type in tool_list:
            if not tool_path in tool_loaded:
                files_to_remove.append(os.path.join(tools_directory, tool_path))

        removal_error = None
        for file_path in files_to_remove:
            try:
                if os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                else:
                    os.remove(file_path)
            except Exception as e:
                removal_error = True
                print("Failed to remove file: {}".format(e))

        if removal_error:
            print("")

        print("Done")
        print("")
        print("OpenCore EFI build complete.")
        time.sleep(2)
        
    def results(self, hardware_report, smbios_model):
        self.u.head("Results")
        print("")
        print("Your OpenCore EFI for {} has been built at:".format(hardware_report.get("Motherboard").get("Name")))
        print("\t{}".format(self.result_dir))
        print("")
        print("Before using EFI, please complete the following steps:")
        print("")
        print("1. Use USBToolBox:")
        print("   - Mapping USB with the option 'Use Native Class' enabled.")
        print("   - Use the model identifier '{}'.".format(smbios_model))
        print("")
        print("2. Add USBMap.kext:")
        print("   - Place the created USBMap.kext file into the {} folder.".format("EFI\\OC\\Kexts" if os.name == "nt" else "EFI/OC/Kexts"))
        print("")
        print("3. Edit config.plist:")
        print("   - Use ProperTree to open your config.plist.")
        print("   - Run OC Snapshot by pressing Command/Ctrl + R.")
        print("   - Save the file when finished.")
        print("")
        self.u.open_folder(self.result_dir)
        self.u.request_input()

    def main(self):
        hardware_report_path = None
        supported_macos_version = None
        unsupported_devices = None
        macos_version = None
        smbios_model = None

        while True:
            self.u.head()
            print("")
            print("Hardware Report: {}".format("No report selected" if not hardware_report_path else hardware_report_path))
            print("")
            if hardware_report_path:
                print("* Hardware Compatibility:")
                if supported_macos_version:
                    print("   - Supported macOS Version: {} - {}".format(os_data.get_macos_name_by_darwin(supported_macos_version[-1]), os_data.get_macos_name_by_darwin(supported_macos_version[0])))
                if unsupported_devices:
                    print("   - Unsupported devices:")
                    for index, device_name in enumerate(unsupported_devices, start=1):
                        device_props = unsupported_devices.get(device_name)
                        print("{}{}. {}{}".format(" "*6, index, device_name, "" if not device_props.get("Audio Endpoints") else " ({})".format(", ".join(device_props.get("Audio Endpoints")))))
                print("* EFI Options:")
                print("   - macOS Version: {}{}".format("Unknown" if not macos_version else os_data.get_macos_name_by_darwin(macos_version), "" if not macos_version else " ({})".format(macos_version)))
                print("   - SMBIOS: {}".format("Unknown" if not smbios_model else smbios_model))
                print("")
            print("1. Select Hardware Report")
            print("2. Select macOS Version")
            print("3. Customize ACPI Patch")
            print("4. Customize Kexts")
            print("5. Customize SMBIOS Model")
            print("6. Build OpenCore EFI")
            print("")
            print("Q. Quit")
            print("")
            option = self.u.request_input("Select an option: ")
            if option.lower() == "q":
                self.u.exit_program()

            try:
                option = int(option)
            except:
                continue

            if option == 1:
                hardware_report_path, hardware_report = self.select_hardware_report()
                supported_macos_version, unsupported_devices = self.c.check_compatibility(hardware_report)
                macos_version = supported_macos_version[-1]
                if int(macos_version[:2]) == os_data.macos_versions[-1].darwin_version and os_data.macos_versions[-1].release_status == "beta":
                    macos_version = str(int(macos_version[:2]) - 1) + macos_version[2:]
                smbios_model = self.s.select_smbios_model(hardware_report, macos_version)
                self.ac.select_acpi_tables()
                self.ac.select_acpi_patches(hardware_report, unsupported_devices, smbios_model)
                self.k.select_required_kexts(hardware_report, smbios_model, macos_version, self.ac.patches)
            elif option < 7:
                try:
                    hardware_report
                except:
                    self.u.request_input("\nPlease select a hardware report to proceed")
                    continue

                if option == 2:
                    macos_version = self.select_macos_version(supported_macos_version)
                    smbios_model = self.s.select_smbios_model(hardware_report, macos_version)
                    self.k.select_required_kexts(hardware_report, smbios_model, macos_version, self.ac.patches)
                elif option == 3:
                    self.ac.customize_patch_selection(hardware_report, unsupported_devices, smbios_model)
                elif option == 4:
                    self.k.kext_configuration_menu(hardware_report, smbios_model, macos_version, self.ac.patches)
                elif option == 5:
                    smbios_model = self.s.customize_smbios_model(hardware_report, smbios_model, macos_version)
                    self.k.select_required_kexts(hardware_report, smbios_model, macos_version, self.ac.patches)
                elif option == 6:
                    self.gathering_files()
                    self.build_opencore_efi(hardware_report, unsupported_devices, smbios_model, macos_version)
                    self.results(hardware_report, smbios_model)

if __name__ == '__main__':
    update_flag = updater.Updater().run_update()
    if update_flag:
        os.execv(sys.executable, ['python3'] + sys.argv)

    o = OCPE()
    while True:
        try:
            o.main()
        except Exception as e:
            o.u.head("An Error Occurred")
            print("")
            print(traceback.format_exc())
            o.u.request_input()