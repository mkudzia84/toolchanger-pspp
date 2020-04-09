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
   

def main():
    if len(sys.argv) < 2:
        print("Usage: tcpspp.py [filename.gcode]")
        return
        
    t_start = time.time()

    filename = sys.argv[1]

    print("-----------------------------------------")
    print(" TC-PSPP : Parsing the file              ")
    gcode = gcode_analyzer.GCodeAnalyzer(filename)

    print("Validating the GCode...")
    validator = gcode_analyzer.GCodeValidator()
    validator.analyze_and_fix(gcode)

    print("-----------------------------------------")
    print(" TC-PSPP : Generating Prime Tower layout ")
    
    tower = prime_tower.PrimeTower()
    tower.analyze_gcode(gcode)
    tower.print_report()

    print(" - Optimizing prime tower layout")
    tower.optimize_layers()
    tower.print_report()
    
    print(" - Injecting Prime Tower GCode")
    tower.inject_gcode()

    print(" TC-PSPS : Optimizing toolhead thermals")
    temp_controller = thermal_control.TemperatureController()
    temp_controller.analyze_gcode(gcode)

    print(" - Injecting Thermal Mangment GCode")
    temp_controller.inject_gcode()

    print(" - Injecting PCF control GCode")
    pcf_controller = pcf_control.PartCoolingFanController()
    pcf_controller.analyze_gcode(gcode)
    pcf_controller.inject_gcode()

    gcode.print_total_runtime()

    print("-----------------------------------------")
    print(" TC-PSPP : Writing modified file...      ")
    with open(filename + '-processed.gcode', mode='w', encoding='utf8') as gcode_out:
        for token in gcode.tokens:
            gcode_out.write(str(token) + '\n')

    t_end = time.time()
    print("TC-PSPP: Done... [elapsed: {elapsed:0.2f}s]".format(elapsed = t_end - t_start))

    time.sleep(5)

# Main entry point
if __name__ == "__main__":

    try:
        main()
    except conf.ConfException as conf_err:
        print("Configuration Error:")
        print(conf_err.message)
        time.sleep(60)
        quit()



    
