import os, math
from logging import Logger
logger = Logger(__name__)

# Configuration exception
class ConfException(Exception):
    def __init__(self, message):
        self.message = message


REMOVE_GCODE = False
PERF_INFO = True
GCODE_VERBOSE = True

#==============================================================================
# Settings to customize by user
retract_lift_speed = 15000              # Retract lift speed in mm/mm

# Printer kinematics settings (check FW setup for RepRap FW)
printer_corexy = True
printer_motor_speed_xy                   = 35000   # XY motor speed in mm/min
printer_motor_speed_z                    = 1200    # Z motor speed  in mm/min

printer_extruder_speed                   = [3600, 3600, 3600, 3600] # Cystomize in mm/min

# Prime tower settings
prime_tower_x = 250.0                   # Prime tower position X
prime_tower_y = 100.0                   # Prime tower position Y
prime_tower_r = 10                      # Prime tower maximum radius
prime_tower_print_speed = 1800          # Prime tower print speed 1800mm/min
prime_tower_move_speed = 35000          # Prime tower move speed (into and out of prime tower)
    
# Prime tower bands 
prime_tower_band_width = 3              # Number of prime tower band width per tool 
prime_tower_band_num_faces = 12         # Prime tower number of faces 
prime_tower_optimize_layers = True      # Enable layer optimization
    
brim_width = 6                          # Number of prime band brims
brim_height = 3                         # How tall should be the brim (number of layers)

# Runtime estimates - tweak
runtime_tool_change = 10                # Fixed time to change the tool [s]
runtime_g10         = 0.4               # z-hop time of 1.2mm at 1200mm/min and retract
runtime_g11         = 0.4               # z-hop time of 1.2mm at 1200mm/min and retract
runtime_default     = 0                 # Default instruction time

# Temp managment
temp_idle_delta     = 30
temp_heating_rate   = 0.6  # Heating rate estimate (in C/s)
temp_cooling_rate   = 0.8  # Cooling rate estimate (in C/s)

wipe_distance    = 0.0       # distance of wipe in mm

#==============================================================================
# Defaults - override while reading settings

tool_temperature_layer0                  = [210,210,210,210]
tool_temperature_layerN                  = [200,200,200,200]
tool_pcfan_disable_first_layers          = [1,1,1,1]
tool_pcfan_speed                         = [1.0, 1.0, 1.0, 1.0]
tool_nozzle_diameter                     = [0.4, 0.4, 0.25, 0.25]
tool_extrusion_multiplier                = [1.0, 1.0, 1.0, 1.0]
tool_filament_diameter                   = [1.75, 1.75, 1.75, 1.75]
    
tool_min_layer_height                    = [0.05, 0.05, 0.05, 0.05]
tool_max_layer_height                    = [0.3, 0.3, 0.2, 0.2]

filament_type                            = ['PLA', 'PLA', 'PLA', 'PLA']
filament_density                         = [1.27, 1.27, 1.27, 1.27]

# Retraction settings
retraction_firmware                      = True
retraction_length                        = [1.2, 1.2, 0.8, 0.8]
retraction_speed                         = [2100, 2100, 2100, 2100]
retraction_zhop                          = [0.6, 0.6, 0.6, 0.6]

relative_E_distances                     = True

bed_temp_layer0                          = [60, 60, 60, 60]
bed_temp_layern                          = [60, 60, 60, 60]

#==============================================================================

# Calculate for specific setup
# For Core XY, 
# Potentially the max speed on a single axis would be superposition of max speed of both motors
# so max speed is between
# - single_motor max speed when movement on diagonal
# - sqrt(2.0) * single motor max speed when movement only on X or Y axis (both motors engaged)
# For temp managment it's better to under-estimate the move time 
# And have the idle tool heat up earlier then over-estimate the time taken and start heating up the tool to late
move_speed_xy = math.sqrt(2.0) * printer_motor_speed_xy if printer_corexy else printer_motor_speed_xy
move_speed_z = printer_motor_speed_z # Z move speed mm/min

# Get max layer height for set of tools 
def max_layer_height(tool_set):
    layer_height = 999.0
    for tool in tool_set:
        if tool_max_layer_height[tool] < layer_height:
            layer_height = tool_max_layer_height[tool]
    # Check if the layer height is valid 
    # i.e. higher then min layer height for the tool set 
    for tool in tool_set:
        if layer_height < tool_min_layer_height[tool]:
            tools = ','.join(['T' + str(tool) for tool in tool_set]),
            raise ConfException("max_layer_height for [{tools}] = {layer_height} lower then min_layer_height for tool T{tool}".format(
                tools = tools, layer_height = layer_height, tool = tool))
      
    return layer_height
        
# Get min layer height for set of tools
def min_layer_height(tool_set):
    layer_height = -999.0
    for tool in tool_set:
        if tool_min_layer_height[tool] > layer_height:
            layer_height = tool_min_layer_height[tool]
    # Check if the layer height is valid
    # i.e. lower then max layer height for the tool set
    for tool in tool_set:
        if layer_height > tool_max_layer_height[tool]:
            tools = ','.join(['T' + str(tool) for tool in tool_set]),
            raise ConfException("min_layer_height for [{tools}] = {layer_height} higher then max_layer_height for tool T{tool}".format(
                tools = tools, layer_height = layer_height, tool = tool))
                    
    return layer_height

# Calculate extrusion length for a distance 
def calculate_E(tool_id, layer_height, distance):
    # Volume to extrude = Area (Diameter * Layer Height) * Distance
    nozzle_radius = float(tool_nozzle_diameter[tool_id]) / 2.0
    V_out = (2.0 * nozzle_radius * distance + math.pi * ((nozzle_radius) ** 2)) * layer_height
    # Extrude Length = (Volume to Extrude / Filament Cross Section Area) * Extrusion Multiplier
    E = ((V_out * 4.0) / (math.pi * (float(tool_filament_diameter[tool_id]) ** 2)) * float(tool_extrusion_multiplier[tool_id]))
        
    return round(E,5)


# Get tool temperature 
def tool_temperature(layer_num, tool_id):
    if layer_num is None or layer_num == 0:
        return tool_temperature_layer0[tool_id]
    else:
        return tool_temperature_layerN[tool_id]

def bed_temperature(layer_num, tools_used):
    bed_temps = []
    if layer_num == 0:
        bed_temps = [bed_temp_layer0[tool] for tool in tools_used]
    else:
        bed_temps = [bed_temp_layern[tool] for tool in tools_used]
    return max(bed_temps)

# Load slic3r settings
def slic3r_config_read():
    global tool_temperature_layer0
    global tool_temperature_layerN
    global tool_pcfan_disable_first_layers
    global tool_pcfan_speed
    global tool_nozzle_diameter
    global tool_extrusion_multiplier
    global tool_filament_diameter
    global tool_min_layer_height
    global tool_max_layer_height
    global filament_type

    # Retraction settings
    global retraction_firmware
    global retraction_length
    global retraction_speed
    global retraction_zhop

    global relative_E_distances

    global bed_temp_layer0
    global bed_temp_layern

    if 'SLIC3R_FIRST_LAYER_TEMPERATURE' in os.environ:
        tool_temperature_layer0                  = [int(t) for t in os.environ['SLIC3R_FIRST_LAYER_TEMPERATURE'].split(',')]
        tool_temperature_layerN                  = [int(t) for t in os.environ['SLIC3R_TEMPERATURE'].split(',')]
        tool_pcfan_disable_first_layers          = [int(l) for l in os.environ['SLIC3R_DISABLE_FAN_FIRST_LAYERS'].split(',')]
        tool_pcfan_speed                         = [float(s) / 100.0 for s in os.environ['SLIC3R_MAX_FAN_SPEED'].split(',')]
        tool_nozzle_diameter                     = [float(d) for d in os.environ['SLIC3R_NOZZLE_DIAMETER'].split(',')]
        tool_extrusion_multiplier                = [float(m) for m in os.environ['SLIC3R_EXTRUSION_MULTIPLIER'].split(',')]
        tool_filament_diameter                   = [float(d) for d in os.environ['SLIC3R_FILAMENT_DIAMETER'].split(',')]
        tool_min_layer_height                    = [float(h) for h in os.environ['SLIC3R_MIN_LAYER_HEIGHT'].split(',')]
        tool_max_layer_height                    = [float(h) for h in os.environ['SLIC3R_MAX_LAYER_HEIGHT'].split(',')]

        filament_type                            = [filament for filament in os.environ['SLIC3R_FILAMENT_TYPE'].split(';')]

        # Retraction settings
        retraction_firmware                      = True if int(os.environ['SLIC3R_USE_FIRMWARE_RETRACTION']) == 1 else False
        retraction_length                        = [float(l) for l in os.environ['SLIC3R_RETRACT_LENGTH'].split(',')]
        retraction_speed                         = [float(s) * 60.0 for s in os.environ['SLIC3R_RETRACT_SPEED'].split(',')]
        retraction_zhop                          = [float(h) for h in os.environ['SLIC3R_RETRACT_LIFT'].split(',')]

        # Settings
        relative_E_distances                     = True if int(os.environ['SLIC3R_USE_RELATIVE_E_DISTANCES']) == 1 else False

        # Bed temperature
        bed_temp_layer0                          = [int(t) for t in os.environ['SLIC3R_FIRST_LAYER_BED_TEMPERATURE'].split(',')]
        bed_temp_layern                          = [int(t) for t in os.environ['SLIC3R_BED_TEMPERATURE'].split(',')]
         
    else:
        logger.warn("Script run outside of PrusaSlicer, using defaults...")


# Validate slic3r settings
def slic3r_config_validate():
    if retraction_firmware == False and relative_E_distances == False:
        raise ConfException("Firmware retraction and relative E distances disabled, if using slicer retraction settings, enable relative E distances")

    # Check tool change retractions
    if 'SLIC3R_RETRACT_LENGTH_TOOLCHANGE' in os.environ and max([int(retraction) for retraction in os.environ['SLIC3R_RETRACT_LENGTH_TOOLCHANGE'].split(',')]) > 0:
            raise ConfException("Slicer has non 0 'Retraction when tool disabled - Length' setting, set it to 0 for all extruders.")

    if 'SLIC3R_WIPE_TOWER' in os.environ and int(os.environ['SLIC3R_WIPE_TOWER']) != 0:
        raise ConfException("Slicer wipe tower enabled, please disable")


  