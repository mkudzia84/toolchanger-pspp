Tool Changer post-processing script 
==========
![enter image description here](https://github.com/mkudzia84/toolchanger-pspp/blob/master/tcppmanual/example.png?raw=true)
This is a post processing script for PrusaSlicer for tool-changing/IDEX multu-extruder setups
using the RepRap3 firmware 

It has been tested on:
PrusaSlicer 2.2
E3D toolchanger with Duet 3 board running RR3.01RC4 firmware with 4 tools

The script includes the following enhancements:
- Robust prime tower generation 
- Smart Active/Idle tool-head temperature management
- PCF speed management
- Validation/stripping of the GCode (i.e. Merlin M900, mapping of the fan ranges etc.)

##### Change Log

###### 30/06/2020
- Fixed issue with prime tower generation that would cause layers to be skipped (FIXED)
- Fixed issue with no prime tower being generated when default (T0) tool is used first

###### 15/04/2020 
Added T{tool}-{filament_type} pair generation into the output file name

#### Prime Tower Generation

The wipe tower functionality in PrusaSlicer is build and optimized for a single extruder/multi-material setup.
As such it works reasonably well with MMU2s or Palette 2 - but not in an IDEX or multi-tool setup.

For a multi-tool setup the prime tower:
- allows to equalize nozzle pressure upon tool activation
- doesn't require excesive material extrusion
- allows for usage of tool-heads of different nozzle diameters
- maintains structural integrity thru-out the print
- doesn't create additional tool change operations

In order to achieve this, the script uses tube structure to generate the prime tower;
Each tool-head is assigned it's own set of shells within the tube - this is done to ensure that, upon priming, material of same properties is depositor upon each other, increasing the strength of the prime tower
In case the tool is not active in that particular layer, the first active tool within that layer is used to fill in the empty shells - this prevents additional tool changes (just for prime tower generation)
Each shell band width is determined based on the tools nozzle diameter

*EXPERIMENTAL - Support of variable layer heights/prime tower layer optimisation*
Even with fixed layer heights, PrusaSlicer offsets support/interface layers vs. the model layers (by between 0.03-0.05mm)
To handle this and the variable layer heights, the script optimises the prime tower structure by combining the subsequent layer tool change priming moves onto a single prime tower layer.
This is done to maximize the prime tower layer height but still keep it within the limits of max layer height for all the tools
active in the layer

#### Smart Active/Idle tool-head temperature management

After generating the prime tower GCode, the post processing script analyzes the result GCode to estimate the run-time of each operation - this is done to determine time periods when each tool is idle between deactivation and activations.

In the script, the user can specify the standby idle temperature delta.
Based on the temperature delta, and configured cooling down and heating up temperature rate estimates - the scripts determines if, within each period of idleness, the idle tool will be able to cool down by temperature delta degrees and then heat-up back to the active temperature before being activated.
If that is the case, it injects GCode to change standby temperatures within the idle period.
This is done to prevent extensive oozing while the tool is idle

The script also inserts GCode to set the temperature of the tool to zero at the last deactivation in the file.

#### Part Cooling Fan management

The script inserts M106 instructions to disable and enable fans upon tool changes - and set appropriate speed settings
based on the Cooling settings of filament assigned to specific tool in PrusaSlicer

## Installation

Dependencies:
- PrusaSlicer 2.2+
- Python 3.8

Download the newest release of the script from the github repository from [main repository](https://github.com/mkudzia84/toolchanger-pspp)

## PrusaSlicer configuration

### Printer Settings:
#### General:

 - Use relative E distances - Checked
 - Use firmware retractions - Checked **[set the retractions/hop in the RR firmware with M207 GCode]**

![enter image description here](https://github.com/mkudzia84/toolchanger-pspp/blob/master/tcppmanual/printer_1.png?raw=true)

#### Extruder 
-  Set Lift Z for Z-hop to 0 **[set the retractions/hop in the RR firmware with M207 GCode]**
- Set Length for Retraction when tool is disabled to 0 **[if needed, set it in your tpostX.g and tfreeX.g scripts with G1 E]**

![enter image description here](https://github.com/mkudzia84/toolchanger-pspp/blob/master/tcppmanual/printer_2.png?raw=true)

#### Custom GCode
##### Start G-Code
    
    ; Park any tool 
    T-1                                       
    ; Home ALL axes
    G28       
    
    ; Lift the nozzle                               
    G1 Z5 F5000 
    
    ; Set Temperature - Bed - Wait
    M140 S[first_layer_bed_temperature] 
    ;; TC_TEMP_INITIALIZE
    
    ; Mesh bed leveling
    G29 
    ; Enable Bed leveling
    G29 S1

;; TC_TEMP_INITIALIZE is a script marker at which the script inserts the initial Temp and PCF management GCode

##### End G-Code

    ;; TC_TEMP_SHUTDOWN
    
    ;Drop Bed
    G91
    G1 Z2 F1000
    G90
    M400
    
    ; Unload the tool
    T-1
    M400
    
    ; Move the Carriage out
    G1 X-20 Y-20
    M400
    
    ; Disable Mesh Compensation.
    G29 S2

;; TC_TEMP_SHUTDOWN is a script marker at which the script inserts heaters shutdown GCode

##### Before layer change G-Code

    ;; BEFORE_LAYER_CHANGE:[layer_num],[layer_z]

;; BEFORE_LAYER_CHANGE is a script marker identifying end of the layer (as generated by Prusa Slicer)

#### After layer change G-Code

    ;; AFTER_LAYER_CHANGE:[layer_num],[layer_z]
   
  ;; AFTER_LAYER_CHANGE is a script marker identifying the beginning of the layer (as generated by PrusaSlicer)

#### Tool change G-Code

    ;; TOOL_BLOCK_END:{previous_extruder}
    T{next_extruder}
    M120
    M98 P"prime.g"
    M121
    ;; TOOL_BLOCK_START:{next_extruder}

;; TOOL_BLOCK_END is a script marker identifying the end of the block where the Tool is active
;; TOOL_BLOCK_START is a script marker identifying the start of the block where the Tool is active

#### Example:
![enter image description here](https://github.com/mkudzia84/toolchanger-pspp/blob/master/tcppmanual/printer_3.png?raw=true)

#### Limitations:
- Only firmware retractions (G10 and G11) are supported - this is needed for a script to effectively track retractions and un-retractions per each tool in order to effectively inject the moves and extrusion moves for the Prime Tower at various inject points - and if needed pre-pend and append retractions and un-retractions when needed.
- Retraction of filament on tool change should be disabled in the PrusaSlicer and handled within the firmware. This is to avoid any blobs or under-extrusions due to the extra GCode injected at various markers.

### Print Settings
#### Multiple Extruders
- Wipe Tower - Disable
![enter image description here](https://github.com/mkudzia84/toolchanger-pspp/blob/master/tcppmanual/print_1.png?raw=true)

### Output options
- Output filename format - do not include the time_estimate, this is appended by the script based on calculated estimates
- Post-processing scripts: {PATH_TO_PYTHON}\python.exe {path_to_script_installation}\tcpspp.py;

![enter image description here](https://github.com/mkudzia84/toolchanger-pspp/blob/master/tcppmanual/print_2.png?raw=true)


## Script configuration

All configuration settings can be found within conf.py

    printer_corexy = True
    printer_motor_speed_xy                   = 14400   # XY motor speed in mm/min as in firmware
    printer_motor_speed_z                    = 1200    # Z motor speed  in mm/min as in firmware
    printer_extruder_speed                   = [7200, 7200, 7200, 7200] # Cystomize in mm/min as in firmware
    
    prime_tower_x = 250.0                   # Prime tower position X
    prime_tower_y = 100.0                   # Prime tower position Y
    prime_tower_r = 15.0                    # Prime tower maximum radius in mm
    prime_tower_print_speed = 1800          # Prime tower print speed 1800mm/min
    prime_tower_move_speed = 12000          # Prime tower move speed (into and out of prime tower)
	prime_tower_band_width = 3              # Number of prime tower band width per tool 
	prime_tower_band_num_faces = 16         # Prime tower number of faces (3 will make prime tower a triangle, 4 a square)
	prime_tower_optimize_layers = True      # Enable prime tower layer optimization
	
    brim_width = 6                          # Number of prime band brims
    brim_height = 3                         # How tall should be the brim (number of layers)
    runtime_tool_change = 10                # Fixed time to change the tool [s], used for runtime estimates
    runtime_default     = 0                 # Other instruction time estimate in [s]
    
    temp_idle_delta     = 30                # Temperature delta in C 
    temp_heating_rate   = 0.6               # Heating rate estimate (in C/s)
    temp_cooling_rate   = 0.8               # Cooling rate estimate (in C/s)

