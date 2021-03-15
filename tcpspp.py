# PRUSA SLICER tool changer post processing script
# Written by Marcin Kudzia 
# https://github.com/mkudzia84
import sys, os, time, math, traceback
from collections import deque 

import conf
import gcode_analyzer
import tool_change_plan
import prime_tower
import thermal_control
import pcf_control

import logging, logging.config
logging.config.fileConfig(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'logger.conf'))

# Build tool_filament name
def tool_filament_names(layer_info):
    return '_'.join(["T{tool_id}-{filament}".format(tool_id = tool, filament = conf.filament_type[tool]) for tool in (layer_info.tools_active | layer_info.tools_idle)])

def main():
    if len(sys.argv) < 2:
        logging.info("Usage: tcpspp.py [filename.gcode]")
        return
        
    t_start = time.time()

    filename = sys.argv[1]

    conf.slic3r_config_read()
    conf.slic3r_config_validate()

    logging.info("-----------------------------------------")
    logging.info(" TC-PSPP : Parsing the file              ")
    gcode = gcode_analyzer.GCodeAnalyzer(filename)

    logging.info("Validating the GCode...")
    validator = gcode_analyzer.GCodeValidator()
    validator.analyze_and_fix(gcode)

    logging.info("-----------------------------------------")
    logging.info(" TC-PSPP : Generating Prime Tower layout ")
    
    tower = prime_tower.PrimeTower()
    tower.analyze_gcode(gcode)
    tower.print_report()

    logging.info(" - Optimizing prime tower layout")
    tower.optimize_layers()
    tower.print_report()
    
    logging.info(" - Injecting Prime Tower GCode")
    tower.inject_gcode()

    logging.info(" TC-PSPS : Optimizing toolhead thermals")
    temp_controller = thermal_control.TemperatureController()
    temp_controller.analyze_gcode(gcode)

    logging.info(" - Injecting Thermal Mangment GCode")
    temp_controller.inject_gcode()

    logging.info(" - Injecting PCF control GCode")
    pcf_controller = pcf_control.PartCoolingFanController()
    pcf_controller.analyze_gcode(gcode)
    pcf_controller.inject_gcode()

    gcode.print_total_runtime()
    gcode.print_total_extrusion()
    gcode.update_statistics()

    # Run validation
    logging.info("Validating...")
    if validator.analyze_retracts(gcode):
        logging.info("[Ok] Retract/unretract sequence")
    else:
        logging.error("[Error] Retract/unretract sequence")

    logging.info("-----------------------------------------")
    logging.info(" TC-PSPP : Writing modified file...      ")
    filename_out = filename[0:filename.rfind('.gcode')] + '_' + tool_filament_names(tower.layers[0]) + '_' + gcode.total_runtime_str + '.gcode'
    logging.info(" Writing to {filename}".format(filename = filename_out))

    with open(filename_out, mode='w', encoding='utf8') as gcode_out:
        for token in gcode.tokens:
            gcode_out.write(str(token) + '\n')


    if conf.REMOVE_GCODE:
        logging.info(" Removing old file {filename}".format(filename = filename))
        os.remove(filename)

    t_end = time.time()
    logging.info("TC-PSPP: Done... [elapsed: {elapsed:0.2f}s]".format(elapsed = t_end - t_start))

    time.sleep(20)

# Main entry point
if __name__ == "__main__":

    try:
        main()
    except conf.ConfException as conf_err:
        logging.error("Configuration error:")
        logging.error("[Error] " + conf_err.message)
        quit()
    except gcode_analyzer.GCodeStateException as gcode_err:
        logging.error("GCode parsing error:")
        logging.error("[Error] " + gcode_err.message)
        quit()




    
