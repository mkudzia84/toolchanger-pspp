from tool_change_plan import LayerInfo, ToolChangeInfo
from gcode_analyzer import Token
import tool_change_plan
import gcode_analyzer
import doublelinkedlist
import conf
import copy, math
from collections import deque

# Function to generate vertices for a circle 
def circle_generate_vertices(cx, cy, radius, num_faces):
    vertices = []

    for indx in range(0, num_faces):
        alpha = 2 * math.pi * float(indx) / num_faces
        x = round(radius * math.cos(alpha) + cx, 3)
        y = round(radius * math.sin(alpha) + cy, 3)
        vertices.append([x, y])
    return vertices 

# Function to Generate a Zig-Zag between two circles
def zigzag_generate_vertices(cx, cy, r1, r2, num_faces):
    v1 = circle_generate_vertices(cx, cy, r1, num_faces)
    v2 = circle_generate_vertices(cx, cy, r2, num_faces)
    v = []

    for indx in range(0, num_faces):
        v.append(v1[indx])
        v.append(v2[indx])
    return v

# Tool change exception
class PrimeTowerException(Exception):
    def __init__(self, message):
        self.message = message 

###########################################################################################################
# Prime Tower Layer Info
class PrimeTowerLayerInfo(LayerInfo):
    def __init__(self, layer_num = 0, layer_z = 0.0, layer_height = 0.0, tool_change_seq = None, prime_tower = None):
        LayerInfo.__init__(self, 
                           layer_num = layer_num, 
                           layer_z = layer_z, 
                           layer_height = layer_height, 
                           tool_change_seq = tool_change_seq)
        self.prime_tower = prime_tower

    # Create tokens for printing a shape
    # Moves to the first point 
    def gcode_print_shape(self, vertices, tool_id, retract_on_move = True, closed = True):
        tokens = doublelinkedlist.DLList()

        if tool_id not in self.tools_active:
            raise gcode_analyzer.GCodeSerializeException("Tool {tool_id} not in active set of prime tower layer #{layer_num}".format(
                tool_id = tool_id,
                layer_num = self.layer_num))
        
        tokens.append_node(gcode_analyzer.GCode('G1', {'X' : vertices[0][0], 'Y' : vertices[0][1]}))
        tokens.append_node(gcode_analyzer.GCode('G1', {'F' : conf.prime_tower_print_speed}))
        for v in range(1, len(vertices)):
            distance = math.sqrt((vertices[v][0] - vertices[v-1][0])**2 + (vertices[v][1] - vertices[v-1][1])**2)
            E = conf.calculate_E(tool_id, self.layer_height, distance)
            tokens.append_node(gcode_analyzer.GCode('G1', {'X' : vertices[v][0], 'Y' : vertices[v][1], 'E' : E}))

        if closed:
            distance = math.sqrt((vertices[-1][0] - vertices[0][0])**2 + (vertices[-1][1] - vertices[0][1])**2)
            E = conf.calculate_E(tool_id, self.layer_height, distance)
            tokens.append_node(gcode_analyzer.GCode('G1', {'X' : vertices[0][0], 'Y' : vertices[0][1], 'E' : E}))

        return tokens
    
    # Create gcode for brim for specific tool
    def gcode_pillar_brim(self, tool_id):
        brim_gcode = doublelinkedlist.DLList()

        # Brint the brim radius
        for circle in self.prime_tower.brim_radiuses[tool_id]:
            circle_vertices = circle_generate_vertices(conf.prime_tower_x, conf.prime_tower_y, circle.radius, conf.prime_tower_band_num_faces)
 
            brim_gcode.append_nodes(self.gcode_print_shape(circle_vertices, tool_id))
            retract = False

        if conf.GCODE_VERBOSE:
            brim_gcode.head.comment = "TC-PSPP - T{tool} - Brim - Start".format(tool = tool_id)
            brim_gcode.tail.comment = "TC-PSPP - T{tool} - Brim - End".format(tool = tool_id)
        
        return brim_gcode

    # Create gcode for band for specific tool
    def gcode_pillar_band(self, tool_id):
        band_gcode = doublelinkedlist.DLList()

        for circle in self.prime_tower.band_radiuses[tool_id]:
            # Start each circle at a different point to avoid weakening the tower
            circle_vertices = deque(circle_generate_vertices(conf.prime_tower_x, conf.prime_tower_y, circle.radius, conf.prime_tower_band_num_faces))
            circle_vertices.rotate(self.layer_num)

            band_gcode.append_nodes(self.gcode_print_shape(circle_vertices, tool_id))
            retract = False

        if conf.GCODE_VERBOSE:
            band_gcode.head.comment = "TC-PSPP - T{tool} - Pillar - Start".format(tool = tool_id)
            band_gcode.tail.comment = "TC-PSPP - T{tool} - Pillar - End".format(tool = tool_id)
        
        return band_gcode

    # Generate gcode for pillar bands for IDLE tools
    def gcode_pillar_idle_tool_bands(self, tool_id):
        circle_bands = [] # tuples (first, last)
        for tool in self.tools_idle:
            circle_bands.append((self.prime_tower.band_radiuses[tool][0], self.prime_tower.band_radiuses[tool][-1]))
        # Combine the circle bands
        circle_bands_optimized = []
        
        i = 0
        for i in range(0, len(circle_bands)):
            c1, c2 = circle_bands[i][0], circle_bands[i][1]
            
            for j in range(i+1, len(circle_bands)):
                # Merge two
                if circle_bands[j][0].band_number == c2.band_number + 1:
                    c2 = circle_bands[j][1]
                else:
                    i = j
                    if conf.DEBUG:
                        print("(DEBUG) PrimeTower: Merged IDLE tool bands onto ({r1}, {r2}) on layer #{layer}".format(r1 = c1.radius, r2 = c2.radius, layer = self.layer_num))
            circle_bands_optimized.append((c1, c2))
                    
        # Generate vertices
        tokens = doublelinkedlist.DLList()

        is_first_band = True
        for band in circle_bands_optimized:
            vertices = zigzag_generate_vertices(conf.prime_tower_x, conf.prime_tower_y, band[0].radius, band[1].radius, conf.prime_tower_band_num_faces)
            gcode_shape = self.gcode_print_shape(vertices, tool_id)
            
            # Add retraction and detractions between the bands
            if is_first_band == False:
                gcode_shape.head.append_node_left(gcode_analyzer.GCode('G10'))
                gcode_shape.head.append_node(gcode_analyzer.GCode('G11'))

            tokens.append_nodes(gcode_shape)

            is_first_band = False

        if conf.GCODE_VERBOSE:
            tokens.head.comment = "TC-PSPP - Prime tower idle tool infill for layer #{layer} - start".format(layer = self.layer_num)
            tokens.tail.comment = "TC-PSPP - Prime tower idle tool infill for layer #{layer} - end".format(layer = self.layer_num)

        return tokens

    # Inject move to prime tower
    # Assumes first gcode in gcode is G1 - move
    def inject_prime_tower_move_in(self, inject_point, gcode):
        # - if prime tower Z is higher then current Z - inject Z move before moving to brim XY
        # - if prime tower Z is lower then current Z - inject Z move after brim XY
        if inject_point.state_post.z == None or inject_point.state_post.z < self.layer_z:
            gcode.head.append_node_left(gcode_analyzer.GCode('G1', { 'Z' : self.layer_z} ))
        elif inject_point.state_post.z > self.layer_z:
            gcode.head.append_node(gcode_analyzer.GCode('G1', { 'Z' : self.layer_z} ))

        # - if was unretracted - add retraction/unretraction around the first move from gcode
        if inject_point.state_post.retracted == gcode_analyzer.GCodeAnalyzer.State.UNRETRACTED:
            gcode.head.append_node_left(gcode_analyzer.GCode('G10'))
            gcode.head.append_node(gcode_analyzer.GCode('G11'))
        
        # - if was retracted, just add unretraction after first move from gcode
        if inject_point.state_post.retracted == gcode_analyzer.GCodeAnalyzer.State.RETRACTED:
            gcode.head.append_node(gcode_analyzer.GCode('G11'))
        gcode.head.append_node_left(gcode_analyzer.GCode('G1', { 'F' : conf.prime_tower_move_speed }))

        return gcode

    # Inject move out of prime tower
    def inject_prime_tower_move_out(self, inject_point, gcode):
        # - if  tool was retracted before entry point - retract
        if inject_point.state_post.retracted == gcode_analyzer.GCodeAnalyzer.State.RETRACTED:
            gcode.append_node(gcode_analyzer.GCode('G10'))

        # - if prime tower is higher then inject point Z, move XY first and then Z
        # - if prime tower is lower then inject point Z, move Z first and then XY
        if inject_point.state_post.z is None:
            raise PrimeTowerException("Malformed GCode - injecting prime tower move-out code where Z is not set")

        # To handle the situation where state is not set initially
        # no need to do move back
        if inject_point.state_post.x != None and inject_point.state_post.y != None:
            gcode.append_node(gcode_analyzer.GCode('G1', { 'F' : conf.prime_tower_move_speed }))
            if inject_point.state_post.z < self.layer_z:
                gcode.append_node(gcode_analyzer.GCode('G1', { 'X' : inject_point.state_post.x, 'Y' : inject_point.state_post.y }))
                gcode.append_node(gcode_analyzer.GCode('G1', { 'Z' : inject_point.state_post.z }))
            elif inject_point.state_post.z > self.layer_z:
                gcode.append_node(gcode_analyzer.GCode('G1', { 'Z' : inject_point.state_post.z }))
                gcode.append_node(gcode_analyzer.GCode('G1', { 'X' : inject_point.state_post.x, 'Y' : inject_point.state_post.y }))
            else:
                gcode.append_node(gcode_analyzer.GCode('G1', { 'X' : inject_point.state_post.x, 'Y' : inject_point.state_post.y }))
            gcode.append_node(gcode_analyzer.GCode('G1', { 'F' : inject_point.state_post.feed_rate}))
        else:
            print("Warning : X/Y position state not present, if this error apears more then once the GCode is malformed")

        return gcode

    # Inject prime tower layer gcode
    def inject_gcode(self):
        
        filled_idle_gaps = False

        # Inject bands and brim for active tools
        if self.layer_num == 0:
            tool_indx = 0
            for tool_change in self.tools_sequence:                
                inject_point = None

                # This is done becuase Prusa Slicer injects first tool activation before entering first layer
                if tool_indx == 0:
                    # Case if the the tool is in sequence later on after squishing - don't need to deprime
                    # as this tool will be primed again
                    if tool_indx in self.tools_sequence[1:]:
                        continue

                    inject_point = self.layer_start
                else:
                    inject_point = tool_change.block_start

                # Generate BRIM and BAND
                gcode_brim = self.gcode_pillar_brim(tool_id = tool_change.tool_id)
                if conf.DEBUG:
                    print("(DEBUG) Generated prime tower brim for layer #0 for T{tool}".format(tool = tool_change.tool_id))
      
                gcode_band = self.gcode_pillar_band(tool_id = tool_change.tool_id)
                if conf.DEBUG:
                    print("(DEBUG) Generated prime tower band for layer #0 for T{tool}".format(tool = tool_change.tool_id))
      
                # Add retract and detract between the two
                gcode_brim.tail.append_node(gcode_analyzer.GCode('G10'))   
                gcode_band.head.append_node(gcode_analyzer.GCode('G11'))
                    
                gcode_idle = None
                if not filled_idle_gaps and len(self.tools_idle) != 0:
                    gcode_idle = self.gcode_pillar_idle_tool_bands(tool_change.tool_id)
                    filled_idle_gaps = True
                    if conf.DEBUG:
                        print("(DEBUG) Generated prime tower idle tools infill for layer #0 with T{tool}".format(tool = tool_change.tool_id))
                # GCode idle already has G11 and G10s
                
                # Finally inject 
                gcode = gcode_brim

                # 1) Move to Z of Prime Tower layer 
                # 2) Check if the tool has been already retracted, if not don't retract again
                gcode = self.inject_prime_tower_move_in(inject_point, gcode) 
                
                # 3) Add the band and idle tools
                gcode.append_nodes(gcode_band)
                if gcode_idle is not None:
                    gcode.append_nodes(gcode_idle)

                # 4) If was retracted - retract
                # 5) Go back to the previous position
                gcode = self.inject_prime_tower_move_out(inject_point, gcode)
                
                inject_point.append_nodes_right(gcode)
                if conf.DEBUG:
                    print("(DEBUG) Generated prime tower band for layer #0 for T{tool}".format(tool = tool_change.tool_id))

                tool_indx += 1
        else:

            # Check if we need to continue constructing the tower
            if len(self.tools_active) == 1 and len(self.tools_idle) == 0:
                if conf.DEBUG:
                    print("(DEBUG) One tool ACTIVE and no more IDLE tools - can stop generating prime tower")
                return 

            tool_indx = 0
            for tool_change in self.tools_sequence:
                inject_point = None

                if tool_indx == 0 and len(self.tool_change_seq) == 0:
                    inject_point = self.layer_end
                elif tool_indx == 0 and len(self.tools_sequence) > 0:
                    # de-prime (fill the gap) - prefered because we use same filament
                    inject_point = tool_change.block_end
                else:
                    inject_point = tool_change.block_start

                # Not gonna happen - because we will cut off earlier but
                if inject_point is None:
                    raise PrimeTowerException("Inject-Point is None...")

               # Generate BAND
                gcode_band = self.gcode_pillar_band(tool_id = tool_change.tool_id)
                if conf.DEBUG:
                    print("(DEBUG) Generated prime tower band for layer #{layer_num} for T{tool}".format(layer_num = self.layer_num, tool = tool_change.tool_id))

                gcode_idle = None
                if not filled_idle_gaps and len(self.tools_idle) != 0:
                    gcode_idle = self.gcode_pillar_idle_tool_bands(tool_change.tool_id)
                    filled_idle_gaps = True
                    if conf.DEBUG:
                        print("(DEBUG) Generated prime tower idle tools infill for layer #{layer_num} with T{tool}".format(layer_num = self.layer_num, tool = tool_change.tool_id))

                # Finally inject 
                gcode = gcode_band

                # 1) Move to Z of Prime Tower layer 
                # 2) Check if the tool has been already retracted, if not don't retract again
                gcode = self.inject_prime_tower_move_in(inject_point, gcode) 
                
                # 3) Add the idle tools
                if gcode_idle is not None:
                    gcode.append_nodes(gcode_idle)

                # 4) If was retracted - retract
                # 5) Go back to the previous position
                gcode = self.inject_prime_tower_move_out(inject_point, gcode)

                inject_point.append_nodes_right(gcode)
                if conf.DEBUG:
                    print("(DEBUG) Generated prime tower band for layer #{layer} for T{tool}".format(layer = self.layer_num, tool = tool_change.tool_id))

                tool_indx += 1

###########################################################################################################
# Prime Tower 
# Contains all the information related to prime tower generation
class PrimeTower:

    def __init__(self, layers = None):
        if layers is not None:
            self.generate_layers(layers)

    # Tool prime tower radiuses
    class CircleOutline:
        def __init__(self, radius, band_number):
            self.radius = radius
            self.band_number = band_number

    # Calculate pillar bands for each tool
    def pillar_bands_generate(self):
        self.band_radiuses = {} # tuples (radius, band_number)
        enabled_tools = self.layers[0].tools_active | self.layers[0].tools_idle

        current_r = conf.prime_tower_r
        band_number = 0
        for tool in sorted(enabled_tools):
            tool_band_radiuses = [0.0] * conf.prime_tower_band_width
            for indx in range(0, conf.prime_tower_band_width):
                current_r -= conf.tool_nozzle_diameter[tool] / 2.0
                tool_band_radiuses[indx] = PrimeTower.CircleOutline(current_r, band_number)
                current_r -= conf.tool_nozzle_diameter[tool] / 2.0
                band_number += 1
            
            self.band_radiuses[tool] = tool_band_radiuses

    # Calculate pillar brims for each tool
    def pillar_brims_generate(self):
        self.brim_radiuses = {} # tuples (radius, band_number)

        current_r = conf.prime_tower_r
        band_number = 0
        for tool in [tool_change.tool_id for tool_change in self.layers[0].tool_change_seq]:
            tool_brim_radiuses = [0.0] * conf.brim_width
            for indx in range(0, conf.brim_width):
                current_r += conf.tool_nozzle_diameter[tool] / 2.0
                tool_brim_radiuses[indx] = PrimeTower.CircleOutline(current_r, band_number)
                current_r += conf.tool_nozzle_diameter[tool] / 2.0
                band_number += 1
           
            self.brim_radiuses[tool] = tool_brim_radiuses
        
    # Analyze the tool status
    def analyze_tool_status(self):
        current_tool = None
        enabled_tools = set()

        # Analyse which tools are active per layer (active = printing)
        for layer_info in self.layers:
            # Reset the statuses
            layer_info.reset_status()

            if current_tool is not None:
                layer_info.tools_active.add(current_tool.tool_id)
            for tool_change in layer_info.tool_change_seq:
                current_tool = tool_change
                layer_info.tools_active.add(tool_change.tool_id)

            # add the tools to enabled tools set 
            enabled_tools |= layer_info.tools_active

        # Analyse which tools are disabled (temp = 0) or idle (on standby) per layer
        for layer_info in reversed(self.layers):
            next_layer = layer_info.layer_num + 1
            prev_layer = layer_info.layer_num - 1
            
            if next_layer == len(self.layers):
                # If it's the last layer 
                # - everything that is not active is disabled
                layer_info.tools_idle = set()
                layer_info.tools_disabled = enabled_tools - layer_info.tools_active
            else:
                # If it's not the last layer
                # - suspended in this layer = (suspended in next layer & active in next layer) & !active in this layer)
                # - disabled in this layer = disabled in next layer & !active in this layer
                layer_info.tools_idle = (self.layers[next_layer].tools_idle | self.layers[next_layer].tools_active) - layer_info.tools_active
                layer_info.tools_disabled = self.layers[next_layer].tools_disabled - layer_info.tools_active

    # Generate the layers for prime tower printing
    def analyze_gcode(self, gcode_analyzer):
        self.layers = [PrimeTowerLayerInfo(prime_tower = self)]

        # Active tool
        current_tool = None            # Tool Change Info
        layer_info = self.layers[-1]   # Layer Info
        for token in gcode_analyzer.analyze():
            # Check if AFTER_LAYER_CHANGE label
            if token.type == Token.PARAMS and token.label == 'AFTER_LAYER_CHANGE':
                current_layer, current_layer_z = token.param[0], token.param[1]
                previous_layer_z = 0.0
                # This is because will put first tool before the AFTER_LAYER_CHANGE-BEFORE_LAYER_CHANGE block
                if current_layer != 0:
                    previous_layer_z = layer_info.layer_z
                    layer_info = PrimeTowerLayerInfo(prime_tower = self)
                    self.layers.append(layer_info)
             
                # Update the value
                layer_info.layer_num = current_layer
                layer_info.layer_z = current_layer_z
                layer_info.layer_height = current_layer_z - previous_layer_z
             
                # Update the values
                layer_info.layer_start = token

                # If current tool is not none 
                if current_tool is not None:
                    self.layers[-1].tools_sequence = [current_tool]
                else:
                    self.layers[-1].tools_sequence = []

                continue

            # Check if BEFORE_LAYER_CHANGE label
            if token.type == Token.PARAMS and token.label == 'BEFORE_LAYER_CHANGE':
                next_layer, next_layer_z = token.param[0], token.param[1]
                # Mark the last layer end as the token before BEFORE_LAYER_CHANGE
                layer_info.layer_end = token.prev

                # Validate the height
                toolset = [tool_change_info.tool_id for tool_change_info in layer_info.tools_sequence]
                toolset_min_layer_height = conf.min_layer_height(toolset)
                toolset_max_layer_height = conf.max_layer_height(toolset)

                # Layer height higher then max for the toolset (shouldn't happen!)
                if layer_info.layer_height > toolset_max_layer_height:
                    raise PrimeTowerException("Input layer #{layer_num} height {layer_height:0.4f} higher then max allowed for the toolset {tools}".format(
                        layer_num = layer_info.layer_num,
                        layer_height = layer_info.layer_height, 
                        tools = ','.join(['T' + str(tool_id) for tool_id in toolset])))
                continue

            # Check if Tool change
            if token.type == Token.TOOLCHANGE:
                if token.state_post.tool_selected != None:
                    current_tool = ToolChangeInfo(tool_change = token)
                    if conf.DEBUG:
                        print("(DEBUG) PrimeTower - Added tool T{tool_id} to layer #{layer_num}".format(tool_id = token.next_tool, layer_num = self.layers[-1].layer_num))

                    layer_info.tool_change_seq.append(current_tool)
                    layer_info.tools_sequence.append(current_tool)
                continue

            # Beginning to Tool block
            if token.type == Token.PARAMS and token.label == 'TOOL_BLOCK_START':
                tool_id = token.param[0]
                if tool_id != -1:
                    if tool_id != current_tool.tool_id:
                        raise ToolChangeException("Tool id {tool_id} from TOOL_BLOCK_START doesn't match last active tool in layer".format(tool_id = tool_id))
                    current_tool.block_start = token
                continue

            # End of Tool block
            if token.type == Token.PARAMS and token.label == 'TOOL_BLOCK_END':
                tool_id = token.param[0]
                if tool_id != -1:
                    if tool_id != current_tool.tool_id:
                        raise ToolChangeException("Tool id {tool_id} from TOOL_BLOCK_END doesn't match last active tool in layer".format(tool_id = tool_id))
                    current_tool.block_end = token
                continue
            

        # Generate the active/idle/disabled list
        #-----------------------------------------------------------
        self.analyze_tool_status()

        # Calc band and brim info
        self.pillar_brims_generate()
        self.pillar_bands_generate()

        return True

    # Optimize the layers of prime tower
    # Squish the layers of prime tower following the rules:
    # For layer {prev,next}
    # - only squish if tool changes in layer next are not in tool changes for layer prev
    # - only squish if layer_height after squish is less then max layer height for new active toolset
    def optimize_layers(self):
        # New layers
        optimized_layers = [self.layers[0]]
        optimized_layer_indx = 0

        for layer_info in self.layers[1:]:

            # 0) number of active tools is just 1 - no need to continue
            if len(layer_info.tools_active) == 1 and len(layer_info.tools_idle) == 0:
                break

            # 1) tool changes not in previous layer tool changes
            prev_layer_tool_change_ids = [tool_info.tool_id for tool_info in optimized_layers[optimized_layer_indx].tool_change_seq]
            next_layer_tool_change_ids = [tool_info.tool_id for tool_info in layer_info.tool_change_seq]

            # Update existing
            if len(set(prev_layer_tool_change_ids) & set(next_layer_tool_change_ids)) == 0:
                # New tool change sequence
                optimized_layer_height = optimized_layers[optimized_layer_indx].layer_height + layer_info.layer_height
                optimized_active_tools = copy.copy(optimized_layers[optimized_layer_indx].tools_active)
                optimized_active_tools.update(next_layer_tool_change_ids)

                min_layer_height = conf.min_layer_height(optimized_active_tools)
                max_layer_height = conf.max_layer_height(optimized_active_tools)

                # 2) new layer height within margins
                if min_layer_height <= optimized_layer_height <= max_layer_height:
                    if conf.DEBUG:
                        print("(DEBUG) optimized layer height : {height:0.2f} within [{min:0.2f},{max:0.2f}] for tools [{tools}]".format(
                            height = optimized_layer_height, 
                            min = min_layer_height, 
                            max = max_layer_height,
                            tools = ','.join([str(tool) for tool in optimized_active_tools])))
                    print("PrimeTower - Prime tower layer #{layer_num} can be combined with previous layer, squashing...".format(layer_num = layer_info.layer_num))

                    # Update the old layer
                    optimized_layers[optimized_layer_indx].tool_change_seq += layer_info.tool_change_seq
                    optimized_layers[optimized_layer_indx].tools_active = optimized_active_tools
                    optimized_layers[optimized_layer_indx].layer_z = layer_info.layer_z
                    optimized_layers[optimized_layer_indx].layer_height = round(optimized_layer_height, 2)
                    optimized_layers[optimized_layer_indx].layer_end = layer_info.layer_end

                    continue

            # Not able to squash - just copying
            optimized_layer_indx += 1
            optimized_layers.append(layer_info)
            optimized_layers[optimized_layer_indx].layer_num = optimized_layer_indx

        # Copy over
        self.layers = optimized_layers

        # Update the statuses
        self.analyze_tool_status()

        return True

    # Inject code into the token list
    def inject_gcode(self):
        # Inject code for all layers
        for layer in self.layers:
            layer.inject_gcode()

    # Generate report on the prime tower composition
    def print_report(self):
        # Dict with layer information
        num_layers_by_height = {}

        for layer in self.layers:
            if layer.layer_height not in num_layers_by_height:
                num_layers_by_height[layer.layer_height] = 1
            else:
                num_layers_by_height[layer.layer_height] += 1

        # Print info
        print("Prime Tower Info :")
        print(" - num layers : {layers_num}".format(layers_num = len(self.layers)))
        
