import conf
from gcode_analyzer import Token, GCodeAnalyzer
import math

# Tool change exception
class ToolChangeException(Exception):
    def __init__(self, message, tool_id):
        self.message = message 
        self.tool_id = tool_id

class ToolChangeInfo:
    def __init__(self, tool_change = None, block_start = None, block_end = None):
        self.tool_id = tool_change.next_tool if tool_change != None else 0
        self.tool_change = tool_change
        self.block_start = block_start
        self.block_end = block_end

# Layer Status
class LayerInfo:
    def __init__(self, layer_num = 0, layer_z = 0.0, layer_height = 0.0, tool_change_seq = None):
        self.layer_num = layer_num
        self.layer_z = layer_z 
        self.layer_height = layer_height
        self.tool_change_seq = tool_change_seq
        if self.tool_change_seq is None:
            self.tool_change_seq = []                     # List of ToolChangeInfo blocks
        self.tools_sequence = []

        self.reset_status()

        self.layer_start = None
        self.layer_end = None

    # Reset the status
    def reset_status(self):
        self.tools_active = set()                         # Identifier set
        self.tools_idle = set()                           # Identifier set
        self.tools_disabled = set()                       # Identifier set

    def __str__(self):
        return "Layer {layer_num},z:{layer_z:0.4f},h:{layer_h:0.4f} : T_change_seq - [{change_seq}], T_active - {active}, T_idle - {idle}, T_disabled = {disabled}".format(
                layer_num = self.layer_num,
                layer_z = self.layer_z,
                layer_h = self.layer_height,
                change_seq = ','.join(['T' + str(tool_change.tool_id) for tool_change in self.tool_change_seq]),
                active = self.tools_active,
                idle = self.tools_idle,
                disabled = self.tools_disabled)
