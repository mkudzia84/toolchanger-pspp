# PRUSA SLICER tool changer post processing script
# Written by Marcin Kudzia 
# https://github.com/mkudzia84

import sys, os, time, math
from collections import deque

# Configuration 
class Conf:
    tool_temperature_layer0                  = [int(t) for t in os.environ['SLIC3R_FIRST_LAYER_TEMPERATURE'].split(',')]
    tool_temperature                         = [int(t) for t in os.environ['SLIC3R_TEMPERATURE'].split(',')]
    tool_temperature_standby_delta           = int(os.environ['SLIC3R_STANDBY_TEMPERATURE_DELTA'])
    tool_pcfan_disable_first_layers          = [int(l) for l in os.environ['SLIC3R_DISABLE_FAN_FIRST_LAYERS'].split(',')]
    tool_pcfan_speed                         = [float(s) / 255.0 for s in os.environ['SLIC3R_MAX_FAN_SPEED'].split(',')]
    tool_nozzle_diameter                     = [float(d) for d in os.environ['SLIC3R_NOZZLE_DIAMETER'].split(',')]
    tool_extrusion_multiplier                = [float(m) for m in os.environ['SLIC3R_EXTRUSION_MULTIPLIER'].split(',')]
    tool_filament_diameter                   = [float(d) for d in os.environ['SLIC3R_FILAMENT_DIAMETER'].split(',')]
    
    retract_lift                             = [float(h) for h in os.environ['SLIC3R_RETRACT_LIFT'].split(',')]
    retract_lift_speed = 15000              # Retract lift speed in mm/min
    
    # Settings 
    prime_tower_x = 150.0                   # Prime tower position X
    prime_tower_y = 100.0                   # Prime tower position Y
    prime_tower_r = 10.0                    # Prime tower maximum radius
    
    # Prime tower bands 
    prime_tower_band_width = 3              # Number of prime tower band width per tool 
    prime_tower_band_num_faces = 36         # Prime tower number of faces 
    
    brim_width = 6                          # Number of prime band brims

    # Calculate extrusion length for a distance 
    def tool_calc_extrusion_length(distance, layer_height, tool_id):
        A_ex = ((float(Conf.tool_nozzle_diameter[tool_id]) - layer_height) * layer_height + math.pi * ((layer_height / 2.0) ** 2))
        V_out = A_ex * distance 
        E = (V_out * 4.0) / (math.pi * (float(Conf.tool_filament_diameter[tool_id]) ** 2) * float(Conf.tool_extrusion_multiplier[tool_id]))
        
        return E
        
    # Calculate extrusion length for a line (using current state)
    def tool_calc_extrusion_line(x1, y1, x2, y2, layer_height, tool_id):
        return Conf.tool_calc_extrusion_length(math.sqrt((x2-x1)**2 + (y2-y1)**2), layer_height, tool_id)
  
# Parse exception
class GCodeParseException(Exception):
    def __init__(self, message, line):
        self.message = message 
        self.line = line 
        
class ToolChangeException(Exception):
    def __init__(self, message, tool_id):
        self.message = message 
        self.tool_id = tool_id
       
# GCODE strings
class GCodeFormats:
    G10_Temp = "G10 P{tool_id} S{active_temp} R{standby_temp}\n"   # Set Tool Active and Standby Temp
    M98 = "M98 P\"{macro}\"\n"                                    # Run macro 
    M104 = "M104 S{temp} T{tool_id}\n"                            # Set Extruder Temp
    M106 = "M106 S{speed}\n"                                      # Set Fan On
    M107 = "M107\n"                                               # Set Fan Off
    M116 = "M116 P{tool_id} S5\n"                                 # Wait for Extruder Temp
    M120 = "M120\n"                                               # Absolute x/y 
    M121 = "M121\n"                                               # Relative x/y 
    G1_EF = "G1 E{E} F{F}\n"                                      # G1 retract 
    G1_Z = "G1 Z{Z}\n"                                            # G1 move Z
    G1_ZF = "G1 Z{Z} F{F}\n"                                      # G1 move Z
    G1_XY = "G1 X{X:.3f} Y{Y:.3f}\n"                              # G1 move 
    G1_XYE = "G1 X{X:.3f} Y{Y:.3f} E{E:.5f}\n"                    # G1 move/extrude
    G1_XYEF = "G1 X{X:.3f} Y{Y:.3f} E{E:.5f} F{F}\n"              # G1 move/extrude/feed rate
    G10_Retract = "G10\n"                                         # G10 retract (Firmware)
    G11 = "G11\n"                                                 # G11 unretract (Firmware)
       
# Class/Namespace for Parsing GCode
class GCodeParser:    
    
    # Gcodes generated by slicer to omit 
    omit = ['M104', 'M109', 'M900']
    
    # Read the Tool change index from the line 
    # return the Integer with the argument 
    # if not match, return None
    def match_T(line):
        line = line.strip()
        if not (len(line) > 1 and line[0] == 'T'):
            return None
            
        comment_npos = line.find(';')
        if comment_npos == -1:
            line = line[1:]
        else:
            line = line[1:comment_npos]
        return int(line)
      
    # Check if is gcode 
    def match_gcode(line, gcode):
        line = line.strip()
        return (len(line) > 1 and gcode in line)
        
    # Read the value from M106 and normalize from 0..255 0.0-1.0 range 
    # Fix for RR3
    # return None if not match
    def match_M106(line):
        if not GCodeParser.match_gcode(line, 'M106'):
            return None
            
        comment_npos = line.find(';')
        if comment_npos != -1:
            line = line[0:comment_npos]
            
        param_pos = line.find('S')
        value = float(line[param_pos+1:]) / 255.0
        return value 
        
    # Check if omit
    def can_omit(line):
        for omit in GCodeParser.omit:
            if GCodeParser.match_gcode(line, omit):
                return True
        return False
      
    # Check if comment contains the tag 
    # Return of tuple of (TagName, Params) if match 
    # Return None if fail
    # Raise exception if number of params doesn't match
    def match_comment_tag(line, tag, num_params = 0, conv = None):
        line = line.strip()
        
        if not (len(line) > 1 and line[0] == ';'):
            return None
        line = line[1:]

        sep_pos = line.find(':')
            
        parsed_params = []
        parsed_tag = line.strip()
            
        if sep_pos != -1:
            parsed_params = line[sep_pos+1:].split(',')
            parsed_tag = line[0:sep_pos].strip()

        if parsed_tag != tag:
            return None
        else:
            if len(parsed_params) != num_params:
                raise GCodeParseException("Tag {tag} doesn't contain arguments, expected {arg_num}".format(tag = tag, arg_num = num_params), line)
            else:
                if num_params == 0:
                    return True
                else:
                    if conv != None:
                        for i in range(0, len(parsed_params)):
                            parsed_params[i] = conv[i](parsed_params[i])
                            
                    return parsed_params

# Class containing information about all the tool changes 
# and when tools are active 
class ToolChangePlan:  
  
    # Map of {layer,[tools]} gcodes changes  
    tool_change_operation  = {}
    
    # Difference between enabled and active 
    # Enabled - tool that may print or may be on standby and will be used in this or future layer 
    # Active - tool that will print within the specific layer
    
    # Map of tools, last layer where the tool is enabled
    # format tool -> last layer when tool is enabled
    tool_enabled = {}
    
    # Map of tools that are active within the layer 
    # format layer -> list of tools
    tool_active = {}
 
  
    # Parse the gcode file for tool changes 
    def parse(gcode_file):
     
        # Populate the tool change operations table 
        with open(gcode_file, mode='r', encoding='utf8') as gcode_in:
        
            # current layer - init layer 0
            current_layer = 0
            ToolChangePlan.tool_change_operation[0] = []
        
            for line in gcode_in.readlines():
            
                # Check if we are in new line
                tag_match = GCodeParser.match_comment_tag(line, 'AFTER_LAYER_CHANGE', 2, [int, float])
                if tag_match != None:
                    current_layer, layer_height = tag_match
                    print("TC-Plan: Processing layer {layer_num}".format(layer_num = current_layer))
                    if current_layer not in ToolChangePlan.tool_change_operation.keys():
                        ToolChangePlan.tool_change_operation[current_layer] = []
                    continue 
                    
                # Check if the line is T-code 
                tool_id = GCodeParser.match_T(line)
                if tool_id != None:
                    print("TC-Plan: Detected T{tool_id} on layer {layer_num}".format(tool_id = tool_id, layer_num = current_layer))
                    ToolChangePlan.tool_change_operation[current_layer].append(tool_id)
                    continue
    
        # Generate the tool active list per layer and tool enabled per tool
        last_active_tool = -1
        for layer in sorted(ToolChangePlan.tool_change_operation.keys()):
            ToolChangePlan.tool_active[layer] = []
            
            if last_active_tool != -1:
                ToolChangePlan.tool_active[layer].append(last_active_tool)
                ToolChangePlan.tool_enabled[last_active_tool] = layer
            for tool_id in ToolChangePlan.tool_change_operation[layer]:
                last_active_tool = tool_id
                if last_active_tool != -1:
                    ToolChangePlan.tool_active[layer].append(last_active_tool)
                    ToolChangePlan.tool_enabled[last_active_tool] = layer 
            
        return True 
       
    # Check if tool is enable within layer n
    def is_tool_enabled(tool_id, layer_num):
        if tool_id not in ToolChangePlan.tool_enabled:
            raise ToolChangeException("Tool not in subset of active tools", tool_id)
        return layer_num <= ToolChangePlan.tool_enabled[tool_id]
       
    # Get list of enabled tools 
    def get_enabled_tools(layer_num = 0):
        if layer_num == 0:
            return ToolChangePlan.tool_enabled.keys()
        else:
            active_tools = []
            for tool_id in sorted(ToolChangePlan.tool_active):
                if ToolChangePlan.is_tool_active(tool_id, layer_num):
                    active_tools.append(tool_id)
            return active_tools
            
    # Get list of disabled tools for layer 0 
    def get_disabled_tools(layer_num):
        # First get list of tools enabled at layer
        enabled = ToolChangePlan.get_enabled_tools(layer_num)
        disabled_tools = []
        for tool_id in sorted(ToolChangePlan.tool_enabled.keys()):
            if tool_id not in enabled:
                disabled_tools.append(tool_id)
        return disabled_tools
 
    # Check if tool is active within layer n
    def is_tool_active(tool_id, layer_num):
        return tool_id in ToolChangePlan.tool_active[layer_num]
        
    def get_active_tools(layer_num):
        return sorted(ToolChangePlan.tool_active[layer_num])

 
    # Generate GCode for layer 0 and wait
    def gcode_temp_managment_layer0():
        gcode = "; TC-PSPP - Layer 0 temperature setup\n"
       
        for tool_id in ToolChangePlan.get_enabled_tools(): # Go over all the tools 
            gcode += GCodeFormats.M104.format(
                tool_id = tool_id, 
                temp = (Conf.tool_temperature_layer0[tool_id] - Conf.tool_temperature_standby_delta))
        for tool_id in ToolChangePlan.get_enabled_tools():
            gcode += GCodeFormats.G10_Temp.format(
                tool_id = tool_id,
                active_temp = Conf.tool_temperature_layer0[tool_id],
                standby_temp = (Conf.tool_temperature_layer0[tool_id] - Conf.tool_temperature_standby_delta))
        for tool_id in ToolChangePlan.get_enabled_tools():
            gcode += GCodeFormats.M116.format(
                tool_id = tool_id)
        gcode += "; TC-PSPP - End temperature setup\n"
        
        return gcode
 
    # Generate GCode for temperature managment
    # TODO: Add smarter STANDBY managment of temperatures 
    def gcode_temp_managment(layer_num):
        gcode = "; TC-PSPP - Extruder Temperature Control\n"
        if layer_num > 0:
            for tool_id in ToolChangePlan.get_enabled_tools():
                if ToolChangePlan.is_tool_enabled(tool_id, layer_num):
                    gcode += GCodeFormats.G10_Temp.format(
                        tool_id = tool_id,
                        active_temp = Conf.tool_temperature[tool_id],
                        standby_temp = (Conf.tool_temperature[tool_id] - Conf.tool_temperature_standby_delta))                                                         
      
        gcode += "; TC-PSPP - End temperature control\n"
        return gcode 
        
# Class for Generating prime tower code 
# Manages the prime tower generation book-keeping
class PrimeTowerPlan:
   
    # Current layer 
    current_layer = 0
    current_layer_z = 0.2
    current_layer_height = 0.2
    current_tool_id = -1
   
    band_radius = {}          # Dictionary of radiuses per tool_id
    band_primed = {}          # Dictionary of bands printed per tool_id
    
    # Layer 0 brim
    brim_radius = {}          # Dictionary of brim bands per tool_id
    
    # Is layer initialized
    layer_initialized = False # Is layer initialized
                                           
 
    # Generate the polygon of specific radius 
    # Return a set of points 
    def polygon_generate_vertices(radius):
        vertices = []
        for indx in range(0, Conf.prime_tower_band_num_faces):
            alpha = 2 * math.pi * float(indx) / Conf.prime_tower_band_num_faces
            x = radius * math.cos(alpha) + Conf.prime_tower_x
            y = radius * math.sin(alpha) + Conf.prime_tower_y
            vertices.append([x, y])
        return vertices 
        
    # Generate tube polygon radiuses
    def band_generate_radius():
        band_radius = {}
        
        current_radius = Conf.prime_tower_r;
        
        for tool_id in ToolChangePlan.get_enabled_tools():
            band_radius[tool_id] = []
            for indx in range(0, Conf.prime_tower_band_width):
                current_radius = current_radius - float(Conf.tool_nozzle_diameter[tool_id]) / 2.0
                band_radius[tool_id].append(current_radius)
                current_radius = current_radius - float(Conf.tool_nozzle_diameter[tool_id]) / 2.0
            
        return band_radius
    
    # Generate brim polygon radiuses
    def brim_generate_radius():
        brim_radius = {}
        
        current_radius = Conf.prime_tower_r;
        
        for tool_id in ToolChangePlan.get_active_tools(layer_num = 0):
            brim_radius[tool_id] = []
            for indx in range(0, Conf.brim_width):
                current_radius = current_radius + float(Conf.tool_nozzle_diameter[tool_id]) / 2.0
                brim_radius[tool_id].append(current_radius)
                current_radius = current_radius + float(Conf.tool_nozzle_diameter[tool_id]) / 2.0
            brim_radius[tool_id].reverse() # Do from outer to inner
                
        return brim_radius
            
    # Generate zhop GCode
    def gcode_move(dest_x, dest_y, hop_to):
        gcode = "; TC-PSPP - Move to {X} {Y}\n".format(X = dest_x, Y = dest_y)
        
        # Todo firmware / nonfirmware retract switch
        if hop_to:
            gcode += GCodeFormats.G1_ZF.format(
                Z = PrimeTowerPlan.current_layer_z + Conf.retract_lift[PrimeTowerPlan.current_tool_id],
                F = Conf.retract_lift_speed)
        gcode += GCodeFormats.G1_XY.format(
            X = dest_x,
            Y = dest_y)
        if hop_to:
            gcode += GCodeFormats.G1_Z.format(
                Z = PrimeTowerPlan.current_layer_z)
        
        return gcode
        
    # Generate the gcode for the vertices 
    # Change the start point of printing of the polygon based on the layer num 
    # Uses current tool 
    def gcode_generate_polygon(radius, tool_id, hop_to = False, unretract = False):
        vertices = PrimeTowerPlan.polygon_generate_vertices(radius)
        
        # Shift in order to avoid start at same spot every layer
        vertex_order = deque(range(0, len(vertices)))
        vertex_order.rotate(PrimeTowerPlan.current_layer % len(vertices))
        
        # Gcode string - hop to the begining
        gcode = PrimeTowerPlan.gcode_move(
            dest_x = vertices[vertex_order[0]][0],
            dest_y = vertices[vertex_order[0]][1],
            hop_to = hop_to)
        vertex_order.rotate(-1) # Shift one 
        
        if unretract:
            gcode += GCodeFormats.G11
        
        # Distance is the same 
        extrusion_length = Conf.tool_calc_extrusion_line(
            x1 = vertices[vertex_order[0]][0], 
            x2 = vertices[vertex_order[1]][0],
            y1 = vertices[vertex_order[0]][1],
            y2 = vertices[vertex_order[1]][1],
            layer_height = PrimeTowerPlan.current_layer_height,
            tool_id = tool_id
            )
        
        for v in vertex_order:
            gcode += GCodeFormats.G1_XYEF.format(
                X = vertices[v][0],
                Y = vertices[v][1],
                E = extrusion_length,
                F = 3600)
                
        return gcode
    
    # Generate gcode for the prime tower band
    # Use currently active toolhead
    # if tool_tube_id is specified - use the current active toolhead 
    # to fill in the band designated for another tool
    # Doesn't generate tool change - just fills the gap
    def gcode_prime_band(tool_tube_id = None):
        if tool_tube_id == None:
            tool_tube_id = PrimeTowerPlan.current_tool_id
        
        if PrimeTowerPlan.band_primed == None:
            return None
            
        # Walkaround for call when T is executed before the 
        # first gcode_start_layer
        if PrimeTowerPlan.layer_initialized == False:
            print("PrimeTowerPlan : Warning - T tool change called before AFTER_LAYER_CHANGE")
            return None
        
        if PrimeTowerPlan.band_primed[tool_tube_id] == True:
            return None
        
        gcode = "; TC-PSPP - Prime Tower Band Start - Tool {tool_id}\n".format(tool_id = PrimeTowerPlan.current_tool_id)
        is_start = True
        for radius in PrimeTowerPlan.band_radius[tool_tube_id]:
            gcode += PrimeTowerPlan.gcode_generate_polygon(radius, PrimeTowerPlan.current_tool_id, hop_to = is_start, unretract = is_start)
            is_start = False
        # Detract
        gcode += GCodeFormats.G10_Retract
        
        PrimeTowerPlan.band_primed[tool_tube_id] = True
        
        gcode += "; TC-PSPP - Prime Tower Band End\n\n"
        
        return gcode 
     
    # Generate gcode to prime missing bands 
    def gcode_prime_missing_bands():
        if PrimeTowerPlan.band_primed == None:
            return None
    
        gcode = "; TC-PSPP - Filling missed bands with {tool_id}\n".format(tool_id = PrimeTowerPlan.current_tool_id)
        for tool_id, primed in PrimeTowerPlan.band_primed.items():
            if primed == False:
                gcode += PrimeTowerPlan.gcode_prime_band(tool_id)
        gcode += "; TC-PSPP - End of Prime Tower bands\n\n"
        
        return gcode
     
    # Generate gcode for the BRIM
    def gcode_prime_brim():
        gcode = "; TC-PSPP - Brim for tool {tool_id}\n".format(tool_id = PrimeTowerPlan.current_tool_id)
        is_start = True
        for radius in PrimeTowerPlan.brim_radius[PrimeTowerPlan.current_tool_id]:
            gcode += PrimeTowerPlan.gcode_generate_polygon(radius, PrimeTowerPlan.current_tool_id, hop_to = is_start, unretract = is_start)
            is_start = False
      
        # Detract
        gcode += GCodeFormats.G10_Retract
            
        gcode += "; TC-PSPP - End of Brim\n\n"
        
        return gcode 
     
    # Generate gcode for start of the layer 
    def gcode_start_layer(layer_num, layer_z, layer_height):
        if PrimeTowerPlan.layer_initialized:
            raise ToolChangeException("PrimeTowerPlan: gcode_start_layer invoked out of order", PrimeTowerPlan.current_tool_id)
    
        gcode = "; TC-PSPP - Layer Start\n"
        # Save the current layer num
        PrimeTowerPlan.current_layer = layer_num
        PrimeTowerPlan.current_layer_z = layer_z 
        PrimeTowerPlan.current_layer_height = layer_height
     
        # If layer 0 - initialize the prime band radiuses 
        if layer_num == 0:
            PrimeTowerPlan.band_radius = PrimeTowerPlan.band_generate_radius()
            PrimeTowerPlan.brim_radius = PrimeTowerPlan.brim_generate_radius()
        
        # Initialize the primed band dictionary
        if len(ToolChangePlan.get_enabled_tools(layer_num)) > 1:
            PrimeTowerPlan.band_primed = {}
        
            for tool_id in ToolChangePlan.get_enabled_tools(layer_num):
                PrimeTowerPlan.band_primed[tool_id] = False
        else:
            PrimeTowerPlan.band_primed = None
        
        PrimeTowerPlan.layer_initialized = True
        
        return gcode
        
    # Generate gcode for the end of the layer 
    def gcode_end_layer():
        if not PrimeTowerPlan.layer_initialized:
            raise ToolChangeException("PrimeTower: gcode_end_layer invoked out of order", PrimeTowerPlan.current_tool_id)
       
        # Check the list of primed bands; 
        # Fill in the gaps with the last active tool
        gcode = PrimeTowerPlan.gcode_prime_missing_bands()
        if gcode == None:
            gcode = ""
        
        gcode = "; TC-PSPP - Layer End\n" + gcode + "; TC-PSPP - Layer End\n"
        
        PrimeTowerPlan.layer_initialized = False
        
        return gcode
    
    # Change the tool
    def tool_change(new_tool_id):
        PrimeTowerPlan.current_tool_id = new_tool_id

     
def main():
    if len(sys.argv) < 2:
        print("Usage: tcpspp.py [filename.gcode]")
        return
        
    filename = sys.argv[1]
   
    print("-----------------------------------------")
    print(" TC-PSPP : Reading tool information      ")
    ToolChangePlan.parse(filename)
    
    for tool_id, layer in ToolChangePlan.tool_enabled.items():
        print("T{tool_id} enabled up untill layer : {layer_num}".format(tool_id = tool_id, layer_num = layer))
           
   
    print("-----------------------------------------")
    print(" TC-PSPP : Prcessing the GCode file      ")
   
        
    # Second pass to Generate the file
    gcode_out = open(filename + ".tmp", mode='w', encoding='utf8')
    
    # Status
    current_layer = None
    old_z = 0.0
    new_z = 0.0
    
    with open(filename, mode='r', encoding='utf8') as gcode_in:
        for line in gcode_in.readlines():
            line = line.strip()
            
            # Check if the GCODE is to be omitted 
            if GCodeParser.can_omit(line):
                continue
                
            # M106 fix
            pcf_speed = GCodeParser.match_M106(line)
            if pcf_speed != None:
                print("TC-PSPP: Fixing M106 value")
                gcode_out.write(GCodeFormats.M106.format(speed = pcf_speed))
                continue

            # Check if INIT code
            tag_match = GCodeParser.match_comment_tag(line, 'TOOLS_INITIALIZE')
            if tag_match != None:
                print("Processed TOOLS_INITIALIZE")
                gcode_out.write(ToolChangePlan.gcode_temp_managment_layer0())
                continue
            
            # Check if BEFORE_LAYER_CHANGE
            tag_match = GCodeParser.match_comment_tag(line, 'BEFORE_LAYER_CHANGE', 2, [int, float])
            if tag_match != None:
                # Add missing prime tower bands
                if current_layer != None:   # Skip on first layer
                    gcode_out.write(PrimeTowerPlan.gcode_end_layer())
                continue
           
            # Check if AFTER_LAYER_CHANGE
            tag_match = GCodeParser.match_comment_tag(line, 'AFTER_LAYER_CHANGE', 2, [int, float])
            if tag_match != None:
                current_layer = tag_match[0]
                old_z = new_z 
                new_z = tag_match[1]
                
                gcode_out.write(PrimeTowerPlan.gcode_start_layer(
                    layer_num = current_layer,
                    layer_z = new_z, 
                    layer_height = new_z - old_z))
                
                # Insert GCode for brim generation
                if current_layer == 0:
                    print("TC-PSPP: Adding BRIM for tool {tool_id}".format(tool_id = PrimeTowerPlan.current_tool_id))
                    gcode_out.write(PrimeTowerPlan.gcode_prime_brim())
                    #gcode_out.write(PrimeTowerPlan.gcode_retract())
                    
                # Insert GCode for temperature management
                # If tool 
                if current_layer > 0:
                    gcode_out.write(ToolChangePlan.gcode_temp_managment(current_layer))
                    
                continue

            # Pre tool change 
            tag_match = GCodeParser.match_comment_tag(line, 'TOOL_CHANGE_PRE', 1, [int])
            if tag_match != None:                
                old_tool_id = tag_match[0]
                if old_tool_id != -1 and current_layer != None:
                    print("TC-PSPP: Adding de-priming band for old tool {tool_id} layer {layer}".format(tool_id = old_tool_id, layer=current_layer))
                    gcode_out.write(PrimeTowerPlan.gcode_prime_band(old_tool_id))
                continue
            
            # Post tool change
            tag_match = GCodeParser.match_comment_tag(line, 'TOOL_CHANGE_POST', 1, [int])            
            if tag_match != None:
                new_tool_id = tag_match[0]
                if new_tool_id != -1:
                    PrimeTowerPlan.tool_change(new_tool_id)
                
                    if current_layer != None:
                        if current_layer == 0:
                            print("TC-PSPP: Adding BRIM for tool {tool_id}".format(tool_id = PrimeTowerPlan.current_tool_id))
                            gcode_out.write(PrimeTowerPlan.gcode_prime_brim())
                            
                            
                        print("TC-PSPP: Adding priming band for new tool {tool_id} layer {layer}".format(tool_id = new_tool_id, layer=current_layer))
                        gcode_out.write(PrimeTowerPlan.gcode_prime_band(new_tool_id)) 
                        #gcode_out.write(PrimeTowerPlan.gcode_retract())
                    
                continue
                
            # Other lines - just write 
            gcode_out.write(line + "\n")
            
    gcode_out.close()

    # delete and rename
    #os.remove(filename)
    #os.rename(filename + ".tmp", filename)
    
# Main loop
main()

    
