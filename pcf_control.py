import conf
import gcode_analyzer
import tool_change_plan
import doublelinkedlist
import time

from gcode_analyzer import Token, GCodeAnalyzer
from tool_change_plan import ToolChangeException
from conf import ConfException

import logging
logger = logging.getLogger(__name__)

# Used to inject GCode for PCF control
class PartCoolingFanController:

    def __init__(self):
        self.tool_change_seq = []

    # Analyze the GCode 
    # the tool change sequence (layer independant)
    def analyze_gcode(self, gcode_analyzer):
        t_start = time.time()

        # Generates the list of tool_activations per tool
        logger.debug("PartFanController: Generating tool activation sequence per tool...")

        # Current tool head
        current_tool = None

        # Go over all of the tokens
        for token in gcode_analyzer.tokens:
            # Setup the tool changes
            if token.type == Token.TOOLCHANGE:
                if token.state_post.tool_selected != None:
                    current_tool = token
                    self.tool_change_seq.append(current_tool)
                continue

        t_end = time.time()
        logger.info("Analysis done [elapsed: {elapsed:0.2f}s]".format(elapsed = t_end - t_start))
    
    # Inject the GCode
    def inject_gcode(self):
        # Go over all the tool changes
        for tool_change in self.tool_change_seq:
            # Disable the old tool
            tool_change.append_node_left(gcode_analyzer.GCode('M106', {'S' : 0}))

            layer_num = tool_change.state_post.layer_num
            if layer_num is not None and layer_num > conf.tool_pcfan_disable_first_layers[tool_change.next_tool]:
                tool_change.append_node(gcode_analyzer.GCode('M106', {'S' : conf.tool_pcfan_speed[tool_change.next_tool]}))
