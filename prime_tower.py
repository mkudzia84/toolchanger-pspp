from tool_change_plan import LayerInfo, ToolChangeInfo
from gcode_analyzer import Token
import tool_change_plan
import gcode_analyzer
import doublelinkedlist
import conf
import copy, math, time
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

    # Create gcode for band for specific tool
    def gcode_pillar_band(self, tool_id):
        band_gcode = doublelinkedlist.DLList()

        for radius in self.prime_tower.get_pillar_bands(self.layer_num, tool_id):
            # Start each circle at a different point to avoid weakening the tower
            circle_vertices = deque(circle_generate_vertices(conf.prime_tower_x, conf.prime_tower_y, radius, conf.prime_tower_band_num_faces))
            circle_vertices.rotate(self.layer_num)

            band_gcode.append_nodes(self.gcode_print_shape(circle_vertices, tool_id))

        if conf.GCODE_VERBOSE:
            band_gcode.head.comment = "TC-PSPP - T{tool} - Pillar - Start".format(tool = tool_id)
            band_gcode.tail.comment = "TC-PSPP - T{tool} - Pillar - End".format(tool = tool_id)
        
        return band_gcode

    # Generate gcode for pillar bands for IDLE tools
    def gcode_pillar_idle_tool_bands(self, tool_id): 
        # Generate vertices
        tokens = doublelinkedlist.DLList()

        for idle_tool_id in self.tools_idle:
            gcode_band = doublelinkedlist.DLList()
            for radius in self.prime_tower.get_pillar_bands(self.layer_num, idle_tool_id):
                vertices = circle_generate_vertices(conf.prime_tower_x, conf.prime_tower_y, radius, conf.prime_tower_band_num_faces)
                gcode_band.append_nodes(self.gcode_print_shape(vertices, tool_id))
                        
            gcode_band.head.append_node(gcode_analyzer.GCode('G11'))
            gcode_band.head.append_node_left(gcode_analyzer.GCode('G10'))

            tokens.append_nodes(gcode_band)

        if conf.GCODE_VERBOSE:
            tokens.head.comment = "TC-PSPP - Prime tower idle tool infill for layer #{layer} - start".format(layer = self.layer_num)
            tokens.tail.comment = "TC-PSPP - Prime tower idle tool infill for layer #{layer} - end".format(layer = self.layer_num)

        return tokens

    # Find wipe end point
    def gcode_wipe_path(self, start_point, length, retract_length):
        path = []
        x0, y0, z0 = start_point.state_post.x, start_point.state_post.y, start_point.state_post.z
        previous_move = start_point

        accumulated_dist = 0.0
        while accumulated_dist < length:
            if previous_move.state_pre == None:
                break
            x1, y1, z1 = previous_move.state_pre.x, previous_move.state_pre.y, previous_move.state_pre.z

            if z1 != z0:
                break

            dist = math.sqrt((x1-x0)**2 + (y1-y0)**2)

            # If end
            if dist + accumulated_dist > length:
                # Check cutoff
                dist_corrected = length - accumulated_dist
                x1 = x0 + (x1 - x0) * (dist_corrected / dist)
                y1 = y0 + (y1 - y0) * (dist_corrected / dist)
                dist = dist_corrected

            if dist != 0.0:
                accumulated_dist += dist
                x0, y0 = x1, y1
                path.append((x1, y1, dist))

            if previous_move.prev is not None:
                previous_move = previous_move.prev

        if accumulated_dist < length:
            print("Warning: Calculated wipe path {wipe_length:0.2f}[mm] less then configured {length:0.2f}[mm]".format(wipe_length = accumulated_dist, length = length))


        # Generate GCodes
        gcode = doublelinkedlist.DLList()

        if len(path) > 2:
            for vertex in path:
                to_retract = -retract_length * (vertex[2]) / accumulated_dist
                gcode.append_node(gcode_analyzer.GCode('G1', {'X' : vertex[0], 'Y' : vertex[1], 'E' : to_retract}))
            print("Calculated wipe path: {length}".format(length = accumulated_dist))
        else:
            # Just add retraction
            gcode.append_node(gcode_analyzer.GCode('G1', {'E', -retract_length}))

        gcode.head.comment = "wipe start"

        return gcode

    # Inject move to prime tower
    # Assumes first gcode in gcode is G1 - move
    def inject_prime_tower_move_in(self, inject_point, gcode):
        inject_state = inject_point.state_post

        gcode_pre = doublelinkedlist.DLList()
        gcode_post = doublelinkedlist.DLList()

        # If firmware retracts - it is quite simple
        if conf.retraction_firmware:
            # - if prime tower Z is higher then current Z - inject Z move before moving to brim XY
            # - if prime tower Z is lower then current Z - inject Z move after brim XY
            if inject_state.z == None or inject_state.z < self.layer_z:
                gcode.head.append_node_left(gcode_analyzer.GCode('G1', { 'Z' : self.layer_z} ))
            elif inject_state.z > self.layer_z:
                gcode.head.append_node(gcode_analyzer.GCode('G1', { 'Z' : self.layer_z} ))

            # - if was unretracted - add retraction/unretraction around the first move from gcode
            if not inject_state.is_retracted:
                gcode.head.append_node(gcode_analyzer.GCode('G11', comment = 'move-in detract'))
                gcode.head.append_node_left(gcode_analyzer.GCode('G10', comment = 'move-in retract' ))
        
            # - if was retracted, just add unretraction after first move from gcode
            if inject_state.is_retracted:
                gcode.head.append_node(gcode_analyzer.GCode('G11', comment = 'move-in detract'))
            gcode.head.append_node_left(gcode_analyzer.GCode('G1', { 'F' : conf.prime_tower_move_speed }))

        # If not firmware retract - do a move manually
        # If not retracted also do a wipe move (not longer then 1mm)
        if not conf.retraction_firmware:
            # If not retracted do a wipe move
            to_detract = 0.0
            if inject_state.is_retracted:
                to_detract = abs(inject_state.retraction)
            else:
                # We will need to detract same what retract in next step
                to_detract = conf.retraction_length[inject_state.tool_selected]

            # If retracted but we need to ajust the Z
            gcode_pre = doublelinkedlist.DLList()
            if inject_state.is_retracted:
                move_z = self.layer_z + conf.retraction_zhop[inject_state.tool_selected]
                if move_z > inject_state.z:
                    gcode_pre.append_node(gcode_analyzer.GCode('G1', {'F', conf.prime_tower_move_speed}))
                    gcode_pre.append_node(gcode_analyzer.GCode('G1', {'Z', move_z }))
            else:
                # Need to retract and Z-hop
                move_z = max(inject_state.z, self.layer_z) + conf.retraction_zhop[inject_state.tool_selected]
                
                gcode_pre.append_node(gcode_analyzer.GCode('G1', {'F' : conf.retraction_speed[inject_state.tool_selected]}))
                # Add the wipe
                if conf.wipe_distance > 0.0:
                    wipe_gcode = self.gcode_wipe_path(inject_point, conf.wipe_distance, conf.retraction_length[inject_state.tool_selected] / 2.0)
                    gcode_pre.append_nodes(wipe_gcode)
                    gcode_pre.append_node(gcode_analyzer.GCode('G1', {'E' : -conf.retraction_length[inject_state.tool_selected] / 2.0}))
                else:
                    gcode_pre.append_node(gcode_analyzer.GCode('G1', {'E' : -conf.retraction_length[inject_state.tool_selected]}))

                gcode_pre.append_node(gcode_analyzer.GCode('G1', {'F', conf.prime_tower_move_speed}))
                gcode_pre.append_node(gcode_analyzer.GCode('G1', {'Z', move_z }))

            # Add the speed
            gcode_pre.append_node(gcode_analyzer.GCode('G1', {'F', conf.prime_tower_move_speed}))

            gcode_post = doublelinkedlist.DLList()
            gcode_post.append_node(gcode_analyzer.GCode('G1', {'Z' : self.layer_z, 'E' : conf.retraction_length[inject_state.tool_selected]}))

            # Add to gcode            
            gcode.head.append_nodes_right(gcode_post)
            gcode.head.append_nodes_left(gcode_pre)

        return gcode

    # Inject move out of prime tower
    def inject_prime_tower_move_out(self, inject_point, gcode):
        inject_state = inject_point.state_post

        # - if prime tower is higher then inject point Z, move XY first and then Z
        # - if prime tower is lower then inject point Z, move Z first and then XY
        if inject_state.z is None:
            raise PrimeTowerException("Malformed GCode - injecting prime tower move-out code where Z is not set")

        # If firmware retracts - it is quite simple
        if conf.retraction_firmware:
            # No need to move out in 
            # -To handle the situation where state is not set initially
            # - Inject point is BEFORE_LAYER_END
            # 
            if inject_point.type != Token.PARAMS or inject_point.label != 'BEFORE_LAYER_CHANGE':
                gcode.append_node(gcode_analyzer.GCode('G10', comment = 'move-out retract'))
                if inject_state.x != None and inject_state.y != None:
                    gcode.append_node(gcode_analyzer.GCode('G1', { 'F' : conf.prime_tower_move_speed }))
                    if inject_state.z < self.layer_z:
                        gcode.append_node(gcode_analyzer.GCode('G1', { 'X' : inject_state.x, 'Y' : inject_state.y }))
                        gcode.append_node(gcode_analyzer.GCode('G1', { 'Z' : inject_state.z }))
                    elif inject_state.z > self.layer_z:
                        gcode.append_node(gcode_analyzer.GCode('G1', { 'Z' : inject_state.z }))
                        gcode.append_node(gcode_analyzer.GCode('G1', { 'X' : inject_state.x, 'Y' : inject_state.y }))
                    else:
                        gcode.append_node(gcode_analyzer.GCode('G1', { 'X' : inject_state.x, 'Y' : inject_state.y }))
                    gcode.append_node(gcode_analyzer.GCode('G1', { 'F' : inject_state.feed_rate}))
                else:
                    print("Warning : X/Y position state not present, if this error apears more then once the GCode is malformed")

                # Retract before 
                # - if  tool was unretracted before entry point - unretract after the move
                if not inject_state.is_retracted:
                    gcode.append_node(gcode_analyzer.GCode('G11', comment = 'move-out detract'))
            else:
                if inject_state.is_retracted:
                    gcode.append_node(gcode_analyzer.GCode('G10', comment = 'move-out retract'))

        # If slicer retracts - more work
        if not conf.retraction_firmware:
            # No need to move out in:
            # - to handle the situation where state is not set initially
            # - Inject point is BEFORE_LAYER_END
            if inject_point.type != Token.PARAMS or inject_point.label != 'BEFORE_LAYER_CHANGE':
                move_z = max(inject_state.z, self.layer_z) + conf.retraction_zhop[inject_state.tool_selected]

                gcode.append_node(gcode_analyzer.GCode('G1', {'F' : conf.retraction_speed[inject_state.tool_selected]}, comment = 'move-out retract'))
                gcode.append_node(gcode_analyzer.GCode('G1', {'E' : -conf.retraction_length[inject_state.tool_selected]}))
                gcode.append_node(gcode_analyzer.GCode('G1', {'F' : conf.prime_tower_move_speed}))
                gcode.append_node(gcode_analyzer.GCode('G1', {'Z' : move_z }))
                gcode.append_node(gcode_analyzer.GCode('G1', {'X' : inject_state.x, 'Y' : inject_state.y}))
                gcode.append_node(gcode_analyzer.GCode('G1', {'Z' : inject_state.z}))
                if not inject_state.is_retracted:
                    gcode.append_node(gcode_analyzer.GCode('G1', {'F' : conf.retraction_speed[inject_state.tool_selected]}))
                    gcode.append_node(gcode_analyzer.GCode('G1', {'E' : conf.retraction_length[inject_state.tool_selected]}))
            else:
                if inject_state.is_retracted:
                    gcode.append_node(gcode_analyzer.GCode('G1', {'F' : conf.retraction_speed[inject_state.tool_selected]}))
                    gcode.append_node(gcode_analyzer.GCode('G1', {'E' : -conf.retraction_length[inject_state.tool_selected]}))
                
        return gcode

    # Inject prime tower layer gcode
    def inject_gcode(self):
        
        filled_idle_gaps = False

        # Check if we need to continue constructing the tower
        if len(self.tools_active) == 1 and len(self.tools_idle) == 0:
            if conf.DEBUG:
                print("(DEBUG) One tool ACTIVE and no more IDLE tools - can stop generating prime tower")
            return 

        tool_indx = 0
        for tool_change in self.tools_sequence:
            inject_point = None

            if tool_indx == 0 and self.layer_num == 0:
                inject_point = self.layer_start
            elif tool_indx == 0 and len(self.tool_change_seq) == 0:
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

            # Info
            gcode.head.append_node_left(gcode_analyzer.Comment("prime-tower layer #{layer_num}".format(layer_num = self.layer_num)))

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
       
    # Generate bands for a layer/tool
    def generate_pillar_bands(self):
        self.band_radiuses = {} 
        self.brim_radiuses = {}

        # Enabled tools - in sequence
        layer0_tools = [tool.tool_id for tool in self.layers[0].tools_sequence] + sorted(self.layers[0].tools_idle)

        # - BRIM
        current_r = conf.prime_tower_r
        for tool in layer0_tools:
            self.brim_radiuses[tool] = []

            for indx in range(0, conf.brim_width):
                current_r += conf.tool_nozzle_diameter[tool] / 2.0
                self.brim_radiuses[tool].append(current_r)
                current_r += conf.tool_nozzle_diameter[tool] / 2.0
        current_r = conf.prime_tower_r
        while current_r > 1.5 * conf.tool_nozzle_diameter[0]:
            current_r -= conf.tool_nozzle_diameter[0] / 2.0
            self.brim_radiuses[tool].insert(0, current_r)
            current_r -= conf.tool_nozzle_diameter[0] / 2.0

        # - BAND
        current_r = conf.prime_tower_r
        for tool in layer0_tools:
            self.band_radiuses[tool] = []

            for indx in range(0, conf.prime_tower_band_width):
                current_r += conf.tool_nozzle_diameter[tool] / 2.0
                self.band_radiuses[tool].append(current_r)
                current_r += conf.tool_nozzle_diameter[tool] / 2.0

    # Get the bands for specific layer
    def get_pillar_bands(self, layer_num, tool_id):
        if layer_num < conf.brim_height:
            return self.brim_radiuses[tool_id]
        else:
            return self.band_radiuses[tool_id]

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

        t_start = time.time()

        # Active tool
        current_tool = None            # Tool Change Info
        layer_info = self.layers[-1]   # Layer Info
        for token in gcode_analyzer.analyze_state():
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
                layer_info.layer_end = token

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
                if token.next_tool != -1:
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
        self.generate_pillar_bands()

        t_end = time.time()
        if conf.PERF_INFO:
            print("PrimeTower: analysis done [elapsed: {elapsed:0.2f}s]".format(elapsed = t_end - t_start))

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
            prev_layer_tool_seq = [tool.tool_id for tool in optimized_layers[optimized_layer_indx].tools_sequence]
            next_layer_tool_seq = [tool.tool_id for tool in layer_info.tools_sequence]

            can_squish = False
            # Can only squish if:
            # - last tool of the previous layer is same as first tool of next layer (i.e. no immediedate change on layer)
            # AND
            # - every tool used in next layer (after first) is different from every tool used in previous layer (except the last)
            if prev_layer_tool_seq[-1] == next_layer_tool_seq[0] and len(set(prev_layer_tool_seq[:-1]) & set(next_layer_tool_seq[1:])) == 0:
                can_squish = True

            # Update existing
            if can_squish:
                # New tool change sequence
                optimized_layer_height = optimized_layers[optimized_layer_indx].layer_height + layer_info.layer_height
                optimized_active_tools = copy.copy(optimized_layers[optimized_layer_indx].tools_active)
                optimized_active_tools.update(next_layer_tool_seq[1:])

                min_layer_height = conf.min_layer_height(optimized_active_tools)
                max_layer_height = conf.max_layer_height(optimized_active_tools)

                # 2) new layer height within margins
                if min_layer_height <= optimized_layer_height <= max_layer_height:
                    # Update the old layer
                    optimized_layers[optimized_layer_indx].tool_change_seq += copy.copy(layer_info.tool_change_seq)
                    optimized_layers[optimized_layer_indx].tools_active = optimized_active_tools
                    optimized_layers[optimized_layer_indx].tools_sequence += layer_info.tools_sequence[1:]
                    optimized_layers[optimized_layer_indx].layer_z = layer_info.layer_z
                    optimized_layers[optimized_layer_indx].layer_height = round(optimized_layer_height, 2)
                    optimized_layers[optimized_layer_indx].layer_end = layer_info.layer_end

                    if conf.DEBUG:
                        print("(DEBUG) optimized layer height : {height:0.2f} within [{min:0.2f},{max:0.2f}] for tools active [{tools}]".format(
                            height = optimized_layer_height, 
                            min = min_layer_height, 
                            max = max_layer_height,
                            tools = ','.join([str(tool.tool_id) for tool in optimized_layers[optimized_layer_indx].tools_sequence])))
                        print("(DEBUG) Prime tower layer #{layer_num} can be combined with previous layer, squashing...".format(layer_num = layer_info.layer_num))
                        print("(DEBUG) >> prev layer tools active : {seq}".format(seq = [str(tool) for tool in prev_layer_tool_seq]))
                        print("(DEBUG) >> next layer tools active : {seq}".format(seq = [str(tool) for tool in next_layer_tool_seq]))

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
        
