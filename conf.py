import os, math

# Configuration exception
class ConfException(Exception):
    def __init__(self, message):
        self.message = message


DEBUG = False
PERF_INFO = True
GCODE_VERBOSE = True

# Imported from Slic3r
tool_temperature_layer0                  = [int(t) for t in os.environ['SLIC3R_FIRST_LAYER_TEMPERATURE'].split(',')]
tool_temperature_layerN                  = [int(t) for t in os.environ['SLIC3R_TEMPERATURE'].split(',')]
tool_pcfan_disable_first_layers          = [int(l) for l in os.environ['SLIC3R_DISABLE_FAN_FIRST_LAYERS'].split(',')]
tool_pcfan_speed                         = [float(s) / 255.0 for s in os.environ['SLIC3R_MAX_FAN_SPEED'].split(',')]
tool_nozzle_diameter                     = [float(d) for d in os.environ['SLIC3R_NOZZLE_DIAMETER'].split(',')]
tool_extrusion_multiplier                = [float(m) for m in os.environ['SLIC3R_EXTRUSION_MULTIPLIER'].split(',')]
tool_filament_diameter                   = [float(d) for d in os.environ['SLIC3R_FILAMENT_DIAMETER'].split(',')]
    
tool_min_layer_height                    = [float(h) for h in os.environ['SLIC3R_MIN_LAYER_HEIGHT'].split(',')]
tool_max_layer_height                    = [float(h) for h in os.environ['SLIC3R_MAX_LAYER_HEIGHT'].split(',')]
    
retract_lift                             = [float(h) for h in os.environ['SLIC3R_RETRACT_LIFT'].split(',')]

# Settings to customize by user
retract_lift_speed = 15000              # Retract lift speed in mm/mm

# Printer kinematics settings (check FW setup for RepRap FW)
printer_corexy = True
printer_motor_speed_xy                   = 14400   # XY motor speed in mm/min
printer_motor_speed_z                    = 1200    # Z motor speed  in mm/min

printer_extruder_speed                   = [7200] * len(retract_lift) # Cystomize in mm/min

# Prime tower settings
prime_tower_x = 250.0                   # Prime tower position X
prime_tower_y = 100.0                   # Prime tower position Y
prime_tower_r = 10.0                    # Prime tower maximum radius
prime_tower_print_speed = 1800          # Prime tower print speed 1800mm/min
prime_tower_move_speed = 12000          # Prime tower move speed (into and out of prime tower)
    
# Prime tower bands 
prime_tower_band_width = 4              # Number of prime tower band width per tool 
prime_tower_band_num_faces = 36         # Prime tower number of faces 
prime_tower_optimize_layers = True      # Enable layer optimization
    
brim_width = 6                          # Number of prime band brims

# Runtime estimates - tweak
runtime_tool_change = 10                # Fixed time to change the tool [s]
runtime_default     = 0                 # Default instruction time 

# Temp managment
temp_idle_delta     = 20
temp_heating_rate   = 0.6  # Heating rate estimate (in C/s)
temp_cooling_rate   = 0.8  # Cooling rate estimate (in C/s)
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
    A_ex = ((float(tool_nozzle_diameter[tool_id]) - layer_height) * layer_height + math.pi * ((layer_height / 2.0) ** 2))
    V_out = A_ex * distance 
    E = (V_out * 4.0) / (math.pi * (float(tool_filament_diameter[tool_id]) ** 2) * float(tool_extrusion_multiplier[tool_id]))
        
    return round(E,5)


# Get tool temperature 
def tool_temperature(layer_num, tool_id):
    if layer_num is None or layer_num == 0:
        return tool_temperature_layer0[tool_id]
    else:
        return tool_temperature_layerN[tool_id]


  