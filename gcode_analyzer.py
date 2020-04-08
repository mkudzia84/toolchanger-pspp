
import doublelinkedlist
import conf
import copy, math

# GCODE strings
class GCodeFormats:
    G10_Temp = "G10 P{tool_id} S{active_temp} R{standby_temp}\n"   # Set Tool Active and Standby Temp
    M98 = "M98 P\"{macro}\"\n"                                    # Run macro 
    M104 = "M104 S{temp} T{tool_id}\n"                            # Set Extruder Temp
    M106 = "M106 S{speed}\n"                                      # Set Fan On
    M107 = "M107\n"                                               # Set Fan Off
    M116 = "M116 P{tool_id} S5\n"                                 # Wait for Extruder Temp
    M120 = "M120\n"                                               # Push position onto stack
    M121 = "M121\n"                                               # Pop position from the stack
    G10_Retract = "G10\n"                                         # G10 retract (Firmware)
    G11 = "G11\n"                                                 # G11 unretract (Firmware)

# Parse exception
class GCodeParseException(Exception):
    def __init__(self, message, line = None):
        self.message = message 
        self.line = line

class GCodeSerializeException(Exception):
    def __init__(self, message):
        self.message = message

# Token 
# Is a double linked list node (makes it easy to iterate
class Token(doublelinkedlist.Node):
    # Token types  
    GCODE                    = 0 # GCode token
    TOOLCHANGE               = 1 # Tool change token
    PARAMS                   = 2 # Params in Comment  ;;Label:p1,p2,p3
    COMMENT                  = 3 # Comment (no params)
        
    def __init__(self, type, runtime_estimate = 0):
        doublelinkedlist.Node.__init__(self)
        self.type = type
        self.runtime_estimate = runtime_estimate
        self.state_pre = None
        self.state_post = None
        self.seq = None
    
# GCode token
class GCode(Token):
    def __init__(self, gcode, param = None, comment = ""):
        Token.__init__(self, type = Token.GCODE)
        self.gcode = gcode
        self.param = param
        if self.param is None:
            self.param = {}
        self.comment = comment
        self.runtime = 0
            
    # Serialize into the str
    def __str__(self):
        return "{gcode} {params} {comment}".format(
            gcode = self.gcode, 
            params = ' '.join([str(k) + str(v) for k, v in self.param.items()]), 
            comment = "; " + self.comment if len(self.comment) > 0 else "")
   
# Tool Change token
class ToolChange(Token):
    def __init__(self, prev_tool, next_tool):
        Token.__init__(self, type = Token.TOOLCHANGE)
        self.prev_tool = prev_tool
        self.next_tool = next_tool

    def __str__(self):
        return "T{next_tool} ; T{prev_tool} -> T{next_tool}".format(
            prev_tool = self.prev_tool, next_tool = self.next_tool)

# Comment - just text
class Comment(Token):
    def __init__(self, text):
        Token.__init__(self, type = Token.COMMENT)
        self.text = text

    # Serialize into str
    def __str__(self):
        return "; " + self.text

# Comment Params
class Params(Token):
    def __init__(self, label, param = []):
        Token.__init__(self, type = Token.PARAMS)
        self.label = label
        self.param = param

    # Seralize into str
    def __str__(self):
        return ";; {label}:{params}".format(
            label = self.label,
            params = ','.join([str(p) for p in self.param]))

# List of tokens gcodes to omit
gcodes_omit = ['M104', 'M109', 'M900']

# params formats 
valid_params_format = {
    'TC_TEMP_INITIALIZE'    : [],
    'BEFORE_LAYER_CHANGE'   : [int, float],
    'AFTER_LAYER_CHANGE'    : [int, float],
    'TOOL_BLOCK_START'      : [int],
    'TOOL_BLOCK_END'        : [int]
    }

# Functions to fix the gcodes
def fix_m106(token):
    token.param['S'] = float(token.param['S']) / 255.0

# gcode fixes
gcodes_fix = {
    'M106' : fix_m106
    }

# GCode analyzer
# Used to iterate over the parsed token list and while collecting the state
class GCodeAnalyzer:

    # GCode state
    class State:

        UNRETRACTED = 1
        RETRACTED = 2

        # Constructor
        def __init__(self):
            self.x = None
            self.y = None
            self.z = None
            self.layer_num = None
            self.feed_rate = None
            self.tool_selected = None
            self.tool_extrusion = {}
            self.retracted = GCodeAnalyzer.State.UNRETRACTED
            # Tweak so it picks up values from Conf
            self.x_movement_absolute = True
            self.y_movement_absolute = True
            self.z_movement_absolute = True
            self.e_relative = True  

        # Copy
        def clone(self):
            return copy.deepcopy(self)

        # Get the move speed
        @property
        def move_speed_x(self):
            if self.feed_rate is not None:
                return min(self.feed_rate, conf.move_speed_xy) 
            else:
                return conf.move_speed_xy

        @property
        def move_speed_y(self):
            if self.feed_rate is not None:
                return min(self.feed_rate, conf.move_speed_xy)
            else:
                return conf.move_speed_xy

        @property
        def move_speed_z(self):
            if self.feed_rate is not None:
                return min(self.feed_rate, conf.move_speed_z)
            else:
                return conf.move_speed_z

        @property 
        def extrud_speed(self):
            if self.tool_selected is None:
                return None
            if self.feed_rate is not None:
                return min(self.feed_rate, conf.printer_extruder_speed[self.tool_selected])
            else:
                return conf.extruder_speed[self.tool_selected]

        # Setter/getter for e
        @property
        def e(self):
            if self.tool_selected is None:
                return 0.0
            else:
                return self.tool_extrusion[self.tool_selected]

        @e.setter
        def e(self, val):
            self.tool_extrusion[self.tool_selected] = val

    # Initialize
    def __init__(self, gcode_file = None):
        if gcode_file is None:
            self.tokens = doublelinkedlist.DLLList()
        else:
            self.parse(gcode_file)
        self.total_runtime = 0

    # Analyze the tokens - from beggining to end
    # Yields the current token
    # State is after GCode execution
    # - also calculates the runtimes
    def analyze(self):
        # State stack - to handle M120 and M121
        # For normal operation - replace the item on on top of the queue
        # for M120 and M121 push and pop copy of the last item onto the stack
        state_stack = [GCodeAnalyzer.State()]
        seq = 0

        for token in self.tokens:
            token.seq = seq
            seq += 1

            # Accumulate the state - replace the top one with the copy
            token.state_pre = state_stack[-1]
            state_stack[-1] = state_stack[-1].clone()
            token.state_post = state_stack[-1]
            
            # Tool change token
            if token.type == Token.TOOLCHANGE:
                if token.next_tool == -1:
                    token.state_post.tool_selected = None
                else:
                    token.state_post.tool_selected = token.next_tool
                
                    # Basically first time the tool is used
                    if token.next_tool not in token.state_post.tool_extrusion:
                        token.state_post.tool_extrusion[token.next_tool] = 0.0

            # GCode 
            if token.type == Token.GCODE:
                # Add retraction
                if token.gcode == 'G10': # Firmware retract
                    token.state_post.retracted = GCodeAnalyzer.State.RETRACTED
                elif token.gcode == 'G11': # Firmware unretract
                    token.state_post.retracted = GCodeAnalyzer.State.UNRETRACTED
                elif token.gcode == 'G1': # Controlled move
                    # TODO: For time being just treat X/Y/Z absolute
                    if 'X' in token.param: token.state_post.x = float(token.param['X'])
                    if 'Y' in token.param: token.state_post.y = float(token.param['Y'])
                    if 'Z' in token.param: token.state_post.z = float(token.param['Z'])
                    if 'E' in token.param:
                        if token.state_pre.e_relative:
                            token.state_post.tool_extrusion[token.state_post.tool_selected] += float(token.param['E'])
                        else: 
                            token.state_post.tool_extrusion[token.state_post.tool_selected] = float(token.param['E'])
                    if 'F' in token.param: token.state_post.feed_rate = float(token.param['F'])
                elif token.gcode == 'M120': # Push state onto stack
                    # Push the copy of the current state onto the stack - experimental
                    state_stack.append(state_stack[-1].clone())
                elif token.gcode == 'M121': # Pop state from the stack 
                    # Pop the copy of the current state from the stack - experimental
                    state_stack.pop()

            # PARAM
            if token.type == Token.PARAMS:
                # Track layer changes
                if token.label == 'AFTER_LAYER_CHANGE':
                    token.state_post.layer_num = token.param[0]

            # Yield result
            yield token
        # end for

    # Build runtime estimates
    def analyze_runtime_estimates(self):
        self.total_runtime = 0.0

        for token in self.analyze():
            # Tool change - set fixed runtime
            if token.type == Token.TOOLCHANGE:
                token.runtime = conf.runtime_tool_change
            elif token.type == Token.GCODE and token.gcode == 'G1':
                # Calculate the runtime based on the move
                s2 = token.state_post
                s1 = token.state_pre
                x2 = s2.x if s2.x != None else 0.0
                x1 = s1.x if s1.x != None else 0.0
                y2 = s2.y if s2.y != None else 0.0
                y1 = s1.y if s1.y != None else 0.0
                z2 = s2.z if s2.z != None else 0.0
                z1 = s1.z if s1.z != None else 0.0
                x_time = abs(x2 - x1) * 120.0 / (s1.move_speed_x + s2.move_speed_x)
                y_time = abs(y2 - y1) * 120.0 / (s1.move_speed_y + s2.move_speed_y)
                z_time = abs(z2 - z1) * 120.0 / (s1.move_speed_z + s2.move_speed_z)
                move_times = [x_time, y_time, z_time]
                if s2.tool_selected is not None:
                    e_time = abs(s2.e - s1.e) * 120.0 / (s1.extrud_speed + s2.extrud_speed)
                    move_times.append(e_time)
                token.runtime = round(max(move_times), 2)

            elif token.type in [Token.COMMENT, Token.PARAMS]:
                token.runtime = 0
            else:
                token.runtime = conf.runtime_default

            yield token
            # Add total runtime
            self.total_runtime += token.runtime

    # Print total runtime
    def print_total_runtime(self):
        runtime_s = int(self.total_runtime)
        runtime_h = math.floor(runtime_s / 3600)
        runtime_m = math.floor((runtime_s % 3600) / 60)
        runtime_s -= (runtime_h * 3600 + runtime_m * 60)  
        print("GCodeAnalyzer: Total runtime estimation: {h}h{m}m{s}s".format(h = runtime_h, m = runtime_m, s = runtime_s))

    # Parse the file and populate the tokens
    def parse(self, gcode_file):
        self.tokens = doublelinkedlist.DLList()

        # Read all the lines        
        with open(gcode_file, mode='r', encoding='utf8') as gcode_in:
            # Track the tool
            current_tool_head = -1

            for line in gcode_in.readlines():
                line = line.strip()

                if len(line) == 0:
                    continue

                # Check if comment
                if line[0] == ';':
                    # Check if comment params - starts with ;;
                    if len(line) > 1 and line[1] == ';':
                        contents = line[2:]
                        # Check if has extra comment - strip
                        comment_pos = contents.find(';')
                        if comment_pos != -1:
                            contents = contents[0:comment_pos].strip()
                        # Check if has params
                        label = None
                        params = []

                        params_sep = contents.find(':')
                        if params_sep != -1:
                            label = contents[0:params_sep].strip()
                            params = contents[params_sep+1:].split(',')
                        else:
                            label = contents.strip()

                        # Check if the label in params
                        if label not in valid_params_format.keys():
                            raise GCodeParseException("Param {label} not valid".format(label = label), line)
                        if len(params) != len(valid_params_format[label]):
                            raise GCodeParseException("Param {label} has invalid number of arguments".format(label = label), line)

                        self.tokens.append_node(Params(
                            label = label,
                            param = [valid_params_format[label][indx](params[indx]) for indx in range(0, len(params))]))
                        continue
                    # Check if normal comment - single ;
                    if len(line) > 1 and line[1] != ';':
                        text = line[1:]

                        self.tokens.append_node(Comment(text = text))
                        continue
                    # Empty comment - skip
                    if len(line) == 1:
                        continue

                # Check if GCODE 
                if line[0] in ['G', 'M']:
                    contents = line
                    comment = ""
                    # Check if has extra comment - strip
                    comment_pos = line.find(';')
                    if comment_pos != -1:
                        contents = line[0:comment_pos].strip()
                        comment = line[comment_pos+1:].strip()

                    # Split into params
                    args = contents.split()

                    # # Check if omit the code
                    gcode = args[0]
                    if gcode in gcodes_omit:
                        continue
                    else:
                        if len(args) == 1:
                            self.tokens.append_node(GCode(
                                gcode = gcode,
                                comment = comment))
                        else:
                            self.tokens.append_node(GCode(
                                gcode = gcode,
                                param = dict([(p[0], p[1:]) for p in args[1:]]),
                                comment = comment))
                        if gcode in gcodes_fix:
                            gcodes_fix[gcode](self.tokens.tail)
                        continue

                # Check if Toolchange
                if line[0] == 'T':
                    # Check if has extra comment - strip
                    contents = line
                    comment_pos = line.find(';')
                    if comment_pos != -1:
                        contents = line[0:comment_pos].strip()
                    
                    previous_tool_head = current_tool_head
                    current_tool_head = int(contents[1:])

                    self.tokens.append_node(ToolChange(
                        prev_tool = previous_tool_head,
                        next_tool = current_tool_head))
                    continue

