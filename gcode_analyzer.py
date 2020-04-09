
import doublelinkedlist
import conf
import copy, math, time                                           # G11 unretract (Firmware)

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

# params formats 
valid_params_format = {
    'TC_TEMP_INITIALIZE'    : [],
    'BEFORE_LAYER_CHANGE'   : [int, float],
    'AFTER_LAYER_CHANGE'    : [int, float],
    'TOOL_BLOCK_START'      : [int],
    'TOOL_BLOCK_END'        : [int]
    }

# GCode analyzer
# Used to iterate over the parsed token list and while collecting the state
class GCodeAnalyzer:

    # GCode state
    class State:

        UNRETRACTED = 1
        RETRACTED = 2

        # Constructor
        def __init__(self, 
                     x = None, 
                     y = None, 
                     z = None, 
                     layer_num = None, 
                     feed_rate = None, 
                     tool_selected = None, 
                     tool_extrusion = None, 
                     retracted = None, 
                     e_relative = True):
            self.x = x
            self.y = y
            self.z = z
            self.layer_num = layer_num
            self.feed_rate = feed_rate
            self.tool_selected = tool_selected
            if tool_extrusion is None:
                self.tool_extrusion = {}
            else:
                self.tool_extrusion = tool_extrusion
            if retracted is None:
                self.retracted = GCodeAnalyzer.State.UNRETRACTED
            else:
                self.retracted = retracted
            self.e_relative = e_relative

        # Copy
        def copy(self):
            lhs = GCodeAnalyzer.State(
                x = self.x,
                y = self.y,
                z = self.z,
                layer_num = self.layer_num,
                feed_rate = self.feed_rate,
                tool_selected = self.tool_selected,
                tool_extrusion = self.tool_extrusion.copy(),
                retracted = self.retracted,
                e_relative = self.e_relative)
            return lhs

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

        # cached list
        self.cached_tokens = []
        
    # Analyze the tokens - from beggining to end
    # State is after GCode execution
    # - also calculates the runtimes
    def analyze_state(self):
        # State stack - to handle M120 and M121
        # For normal operation - replace the item on on top of the queue
        # for M120 and M121 push and pop copy of the last item onto the stack
        state_stack = [GCodeAnalyzer.State()]
        seq = 0

        # Total runtime of GCode
        self.total_runtime = 0.0

        for token in self.tokens:
            token.seq = seq
            seq += 1

            # Accumulate the state - replace the top one with the copy
            token.state_pre = state_stack[-1]
            state_stack[-1] = state_stack[-1].copy()
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
                token.runtime = conf.runtime_tool_change
            # GCode 
            elif token.type == Token.GCODE:
                # Add retraction
                if token.gcode == 'G10': # Firmware retract
                    token.state_post.retracted = GCodeAnalyzer.State.RETRACTED
                    token.runtime = conf.runtime_default
                elif token.gcode == 'G11': # Firmware unretract
                    token.state_post.retracted = GCodeAnalyzer.State.UNRETRACTED
                    token.runtime = conf.runtime_default
                elif token.gcode == 'G1': # Controlled move

                    # Move times
                    token.runtime = 0
                    # TODO: For time being just treat X/Y/Z absolute
                    state_pre = token.state_pre
                    state_post = token.state_post

                    if 'F' in token.param: state_post.feed_rate = float(token.param['F'])
                    if 'X' in token.param: 
                        state_post.x = float(token.param['X'])
                        x0 = state_pre.x if state_pre.x != None else 0.0
                        x_time = abs(state_post.x - x0) * 120.0 / (state_pre.move_speed_x + state_post.move_speed_x)
                        if x_time > token.runtime: token.runtime = x_time
                    if 'Y' in token.param: 
                        state_post.y = float(token.param['Y'])
                        y0 = state_pre.y if state_pre.y != None else 0.0
                        y_time = abs(state_post.y - y0) * 120.0 / (state_pre.move_speed_y + state_post.move_speed_y)
                        if y_time > token.runtime: token.runtime = y_time
                    if 'Z' in token.param: 
                        state_post.z = float(token.param['Z'])
                        z0 = state_pre.z if state_pre.z != None else 0.0
                        z_time = abs(state_post.z - z0) * 120.0 / (state_pre.move_speed_z + state_post.move_speed_z)
                        if z_time > token.runtime: token.runtime = z_time
                    if 'E' in token.param:
                        tool_id = state_pre.tool_selected
                        if state_pre.e_relative:
                            state_post.tool_extrusion[tool_id] += float(token.param['E'])
                        else: 
                            state_post.tool_extrusion[tool_id] = float(token.param['E'])
                        e0 = state_pre.tool_extrusion[tool_id]
                        e1 = state_post.tool_extrusion[tool_id]
                        e_time = abs(e1 - e0) * 120.0 / (state_pre.extrud_speed + state_post.extrud_speed)
                        if e_time > token.runtime: token.runtime = e_time

                elif token.gcode == 'M120': # Push state onto stack
                    # Push the copy of the current state onto the stack - experimental
                    state_stack.append(state_stack[-1].copy())
                    token.runtime = 0.0
                elif token.gcode == 'M121': # Pop state from the stack 
                    # Pop the copy of the current state from the stack - experimental
                    state_stack.pop()
                    token.runtime = 0.0

            # PARAM
            elif token.type == Token.PARAMS:
                # Track layer changes
                if token.label == 'AFTER_LAYER_CHANGE':
                    token.state_post.layer_num = token.param[0]
                token.runtime = 0
            else:
                token.runtime = conf.runtime_default

            # Add the total runtime
            self.total_runtime += token.runtime

        return self.tokens

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
                    gcode = args[0]
                    # # Check if omit the code
                    if len(args) == 1:
                        self.tokens.append_node(GCode(
                            gcode = gcode,
                            comment = comment))
                    else:
                        self.tokens.append_node(GCode(
                            gcode = gcode,
                            param = dict([(p[0], p[1:]) for p in args[1:]]),
                            comment = comment))
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


# GCode validator
# Used to fix the GCode coming out of Prusa
class GCodeValidator:

    gcodes_to_omit = ['M104', 'M109', 'M900']

    # Init
    def __init__(self):
        pass

    # analyze the gcode
    def analyze_and_fix(self, gcode_analyzer):
        
        # found T
        found_tool = False

        # location of TC_INIT
        first_layer_header = None

        # Go over each token
        for token in gcode_analyzer.tokens:

            # gcodes to omit - delete
            if token.type == Token.GCODE and token.gcode in GCodeValidator.gcodes_to_omit:
                if conf.DEBUG:
                    print("(DEBUG) GCodeValidator: Deleting {token}".format(token = str(token)))
                gcode_analyzer.tokens.remove_node(token)
                continue

            # Token to fix 
            if token.type == Token.GCODE and token.gcode == 'M106':
                if conf.DEBUG:
                    print("(DEBUG) GCodeValidator: Fixing M106 from 0..255 to 0-1.0 range")
                token.param['S'] = float(token.param['S']) / 255.0
                continue

            # This is for case where file is using just one tool that is T0
            # PS is assuming that default tool T0 is always enabled....
            # 1) We need to record the location of first layer 
            if token.type == Token.PARAMS and token.label == 'BEFORE_LAYER_CHANGE' and first_layer_header is None:
                first_layer_header = token
                continue

            # 2) If found tool
            if token.type == Token.TOOLCHANGE and token.next_tool != -1:
                found_tool = True

        # Inject the tool change to T0
        if found_tool == False:
            print("Warning! - GCodeValidator: Didn't found a tool change instruction, injecting T0 as a default tool...")
            first_layer_header.append_node_left(ToolChange(-1, 0))
    

