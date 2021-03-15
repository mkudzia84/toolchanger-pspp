import conf
import gcode_analyzer
import tool_change_plan
import doublelinkedlist

import time

from gcode_analyzer import Token, GCodeAnalyzer
from tool_change_plan import ToolChangeInfo, ToolChangeException
from conf import ConfException

# Tool change exception
class ThermalControlException(Exception):
    def __init__(self, message):
        self.message = message

import logging
logger = logging.getLogger(__name__)

# inject temperatures control into the gcode
#
# Strategy goals:
# Have Tmax temperature before tool is activated
# Have Tidle temperatue when tool is not active
# * Assume a linear temperature ramp-up to Tmax with rate temp_heating_rate slope
# - In reality this slope is more logarithmic (based on power of heating cartridge)
# * Assume a linear temperature ramp-down to Tmin with rate temp_cooling_rate slope
# - In reality this slope is more exponential 
#
# Following strategy is used:
# for each tool change
# Ta - target temperature for tool activation
# Td - temperature for tool when it's deactivated
# Ti - idle temperature
# Cr - Cooling rate (in C/s) from conf
# Hr - Heating rate (in C/s) from conf
# 
# let's assume t0 = 0 is the time when tool has been deactivated
# let's assume t1 = t_delta is the time when the tool has been activated
# cooling down function is 
# Tc = -Cr * t + Td
# Heating up function is 
# Th = Hr * (t - t_delta) + Ta
# 
# Point for cooling down when it reaches Ti is 
# tc_idle = (Td - Ti) / Cr
# Point for heating up where heating up temperature is Ti is 
# th_idle = (Ti - Ta) / Hr + t_delta
#
# Following scenarios:
# 1) th_idle < tc_idle => tool on deactivation will not reach the idle temperature before 
#                         having to start heating up 
#    - calculate intersection of Tc and Th:
#      tx = [(Ta - Td) - Hr * t_delta] / (Cr + Hr)
#      if tx < 0 => target temperature on tool activation will not be reached
#                   in time, insert Gcode to wait for temp before tool change
# 2) tc_idle < th_idle => tool will have a period where Ti can be reached
#                         - at Tool deactivation insert GCode to set Standby temperature to Temp Idle
#                         - at point that is at least t_delta - th_idle from Tool activation insert GCode to set Standby to Target temp
#
# Extra special cases:
# A) if there is no tool deactivation found before 
#    - calculate th_idle and insert before Tool activation
#      - if th is before the TC_TEMP_INITIALIZE in the file - insert Target (not idle) temperature in the file header
#      - else insert idle temp at TC_TEMP_INITALIZE

# Contains information about sequence of tool changes 
class TemperatureController:

    def __init__(self):
        self.tool_activation_seq = {}
        self.temp_header = None
        self.temp_footer = None
        self.temp_layer1 = None

    # Analyze the layer information and generate 
    # the tool change sequence (layer independant)
    def analyze_gcode(self, gcode_analyzer):
        t_start = time.time()

        # Generates the list of tool_activations per tool
        logger.debug("Generating tool activation sequence per tool...")

        # Go over the tokens to generate the Tool Change Info 
        # Calculate the runtimes in the process
        logger.info("Estimating the gcode runtimes")

        # Current tool head
        current_tool = None

        # Go over all of the tokens
        for token in gcode_analyzer.analyze_state():
            # Find the location of ;; TC_TEMP_INITIALIZE
            if token.type == Token.PARAMS and token.label == 'TC_TEMP_INITIALIZE':
                self.temp_header = token
                continue

            # Find the location of ;; TC_TEMP_SHUTDOWN
            if token.type == Token.PARAMS and token.label == 'TC_TEMP_SHUTDOWN':
                self.temp_footer = token
                continue

            # Find the first layer start
            if token.type == Token.PARAMS and token.label == 'BEFORE_LAYER_CHANGE' and token.param[0] == 1:
                self.temp_layer1 = token
                continue

            # Remove the existing tokens for temp managment
            if token.type == Token.GCODE and token.gcode == 'M109':
                logger.info("Removed an existing M109 gcode")
                gcode_analyzer.tokens.remove_node(token)
                continue

            # Setup the tool changes
            if token.type == Token.TOOLCHANGE:
                if token.state_post.tool_selected != None:
                    current_tool = ToolChangeInfo(tool_change = token)
                    if current_tool.tool_id not in self.tool_activation_seq:
                        self.tool_activation_seq[current_tool.tool_id] = []
                    self.tool_activation_seq[current_tool.tool_id].append(current_tool)
                continue

            # Beginning to Tool block
            if token.type == Token.PARAMS and token.label == 'TOOL_BLOCK_START':
                tool_id = token.param[0]
                if tool_id != -1:
                    if tool_id != current_tool.tool_id:
                        raise ToolChangeException(message = "Tool id {tool_id} from TOOL_BLOCK_START doesn't match last active tool in layer".format(tool_id = tool_id), tool_id = tool_id)
                    current_tool.block_start = token
                continue

            # End of Tool block
            if token.type == Token.PARAMS and token.label == 'TOOL_BLOCK_END':
                tool_id = token.param[0]
                if tool_id != -1:
                    if tool_id != current_tool.tool_id:
                        raise ToolChangeException(message = "Tool id {tool_id} from TOOL_BLOCK_END doesn't match last active tool in layer".format(tool_id = tool_id), tool_id = tool_id)
                    current_tool.block_end = token
                continue

        if self.temp_header is None:
            raise ConfException("TempController: Did not found TC_TEMP_INITIALIZE parameter in the GCode, slicer has not been configured correctly...")
        if self.temp_footer is None:
            raise ConfException("TempController: Did not found TC_TEMP_SHUTDOWN parameter in the GCode, slicer has not been configured correctly...")

        t_end = time.time()
        logger.info("Analysis done [elapsed: {elapsed:0.2f}s]".format(elapsed = t_end - t_start))

    # Prep tool layer intialization
    def gcode_prep_header(self):
        # Prepare gcode in the header
        gcode_init = doublelinkedlist.DLList()
        gcode_wait = doublelinkedlist.DLList()

        # Check the runtime estimate between TC_TEMP_INITIALIZE and first tool activation
        for tool_id, activation_seq in self.tool_activation_seq.items():
            tool_info = activation_seq[0]

            time_delta = 0.0
            token = self.temp_header.next
            while token != tool_info.tool_change:
                time_delta += token.runtime
                token = token.next

            logger.debug("INIT -> T{tool} - runtime estimate: {delta:0.2f}".format(tool = tool_id, delta = time_delta))

            tool_temp = conf.tool_temperature(tool_info.tool_change.state_pre.layer_num, tool_id)
            # Check if should set idle temp or tool temp at INIT point
            # temp_idle = tool_temp - temp_idle_delta
            time_temp_idle2tool = float(conf.temp_idle_delta) / float(conf.temp_heating_rate)

            if time_temp_idle2tool < time_delta:
                # Find the inject point 
                acc_time = 0.0
                inject_point = tool_info.tool_change.prev
                while inject_point is not None:
                    acc_time += inject_point.runtime
                    if acc_time >= time_temp_idle2tool:
                        break
                    inject_point = inject_point.prev

                logger.debug("Inject point for T{tool} is before \"{token}\" - time diff: {delta:0.2f}s".format(tool = tool_id, token = str(inject_point), delta = acc_time))

                # Insert idle temp in TC_INIT
                # Insert ramp up at inject point
                # Insert temp wait before tool change
                gcode_init.append_node(gcode_analyzer.GCode('G10', {'P' : tool_id, 'R' : tool_temp - conf.temp_idle_delta, }))
                gcode_wait.append_node(gcode_analyzer.GCode('M116', {'P' : tool_id, 'S' : 5}))

                inject_point.append_node(gcode_analyzer.GCode('G10', {'P' : tool_id, 'R' : tool_temp}))
                tool_info.tool_change.append_node_left(gcode_analyzer.GCode('M116', {'P' : tool_id, 'S' : 5}))
            else:
                logger.debug("Inject point for T{tool} at TC_INIT".format(tool = tool_id))

                # Insert target temp at TC_INIT
                # Insert temp wait at TC_INIT
                gcode_init.append_node(gcode_analyzer.GCode('G10', {'P' : tool_id, 'R' : tool_temp}))
                gcode_wait.append_node(gcode_analyzer.GCode('M116', {'P' : tool_id, 'S' : 5}))

        # Inject code for bed temperature 
        gcode_init.append_node(gcode_analyzer.GCode('M140', {'S' : conf.bed_temperature(0, self.tool_activation_seq.keys())}))
        gcode_wait.append_node(gcode_analyzer.GCode('M190'))

        # Inject the gcode at TC_INIT
        self.temp_header.append_nodes_right(gcode_wait)
        self.temp_header.append_nodes_right(gcode_init)
        
    # Prep tool activation/deactivation/idling gcode
    def gcode_prep_toolchange(self):
        # Check the runtime estimate between subsequent tool changes 
        for tool_id, activation_seq in self.tool_activation_seq.items():

            # Check each activation sequence
            for activation_indx in range(1, len(activation_seq)):
                tool_prev_info = activation_seq[activation_indx-1]
                tool_next_info = activation_seq[activation_indx]

                # Calculate the time delta between the deactivation and the activation
                time_delta = 0.0
                token = tool_prev_info.block_end.next
                while token != tool_next_info.tool_change:
                    time_delta += token.runtime
                    token = token.next

                logger.debug("T{tool} block_end -> T{tool} activation - runtime estimate: {delta:0.2f}s".format(tool = tool_id, delta = time_delta))

                # Get the temps
                prev_temp = conf.tool_temperature(tool_prev_info.block_end.state_post.layer_num, tool_id)
                next_temp = conf.tool_temperature(tool_next_info.tool_change.state_pre.layer_num, tool_id)

                # Idle temp - avg of the two minus the delta
                idle_temp = (prev_temp + next_temp) / 2.0 - conf.temp_idle_delta
                
                # Cooldown time
                time_cooling = (prev_temp - idle_temp) / conf.temp_cooling_rate
                time_heating = (next_temp - idle_temp) / conf.temp_heating_rate

                time_idling = time_delta - (time_cooling + time_heating)
                if time_idling <= 0.0:
                    # No idle time - check if there is temp difference between the two
                    if prev_temp < next_temp:
                        time_cooling = 0
                        time_heating = (next_temp - prev_temp) / conf.temp_heating_rate
                        if time_heating >= time_delta:
                            # Heating will take longer the difference - ramp up immedietly
                            idle_temp = next_temp
                        else:
                            # Heating will take less, keep current idle temp
                            idle_temp = prev_temp
                    elif prev_temp > next_temp:
                        idle_temp = next_temp
                        time_cooling = (prev_temp - next_temp) / conf.temp_cooling_rate
                        time_heating = 0
                        # Temp lower, immedietly try to ramp down temp
                        idle_temp = next_temp
                    else:
                        # Equal - nothing to do
                        idle_temp = next_temp
                        time_cooling = 0.0
                        time_heating = 0.0
                    time_idling = time_delta - (time_cooling + time_heating)

                # Statistics
                logger.debug("T{tool} {T_prev}C->{T_idle}C cooling time: {t_cooling:0.2f}s, idle time: {t_idling:0.2f}, {T_idle}C->{T_next}C heating time: {t_heating:0.2f}".format(
                        tool = tool_id, T_prev = prev_temp, T_next = next_temp, T_idle = idle_temp, t_cooling = time_cooling, t_idling = time_idling, t_heating = time_heating))

                # Use the new heating time
                if time_heating > 0.0:
                    # Find the injection point for next temp
                    acc_time = 0.0
                    inject_point = tool_next_info.tool_change.prev
                    while inject_point is not None:
                        acc_time += inject_point.runtime
                        if acc_time >= time_heating:
                            break
                        inject_point = inject_point.prev

                    logger.debug("Inject point for T{tool} temp ramp-up is before \"{token}\" - time diff: {delta:0.2f}s".format(
                            tool = tool_id, token = str(inject_point), delta = acc_time))
                    inject_point.append_node(gcode_analyzer.GCode('G10', {'R' : next_temp, 'T' : tool_id}))

                # Inject the idle temp
                tool_prev_info.block_end.append_node(gcode_analyzer.GCode('G10', {'R' : idle_temp, 'T' : tool_id}))
                tool_next_info.tool_change.append_node_left(gcode_analyzer.GCode('M116', {'P' : tool_id, 'S' : 5}))

    # Prep tool deactivation
    def gcode_prep_deactivation(self):
        # For each tool add disable block
        for tool_id, activation_seq in self.tool_activation_seq.items():
            tool_info = activation_seq[-1]

            if tool_info.block_end is not None:
                logger.info("Disabling T{tool} at layer {layer}".format(
                    tool = tool_id, layer = tool_info.block_end.state_post.layer_num))

                tool_info.block_end.append_node(gcode_analyzer.GCode('G10', {'R' : 0, 'T' : tool_id}))

        # Insert deactivation at the end
        for tool_id in self.tool_activation_seq.keys():
            self.temp_footer.append_node(gcode_analyzer.GCode('G10', {'R' : 0, 'T' : tool_id}))
        self.temp_footer.append_node(gcode_analyzer.GCode('M140', {'S' : 0}))

    # Set the bed temperatures 
    def gcode_prep_bed_temp(self):
        self.temp_layer1.append_node(gcode_analyzer.GCode('M140', {'S' : conf.bed_temperature(1, self.tool_activation_seq.keys())}))
        self.temp_layer1.append_node(gcode_analyzer.GCode('M190'))

    # Inject the GCode
    def inject_gcode(self):
        self.gcode_prep_header()
        self.gcode_prep_bed_temp()
        self.gcode_prep_toolchange()
        self.gcode_prep_deactivation()
