# Iterable double linked list 
# Insert operations are O(1)

# Double linked list Node (as inheritable)
class Node:
    def __init__(self, prev = None, next = None):
        self.dll = None
        self.prev = prev
        self.next = next

    # Node append item left
    def append_node_left(self, node):
        self.dll.append_node_left_of(self, node)

    # Node append item right
    def append_node(self, node):
        self.dll.append_node_at(self,node)

    # Append nodes list on the left
    def append_nodes_left(self, iterable):
        for node in iterable:
            self.dll.append_node_left_of(self, node)

    # Append nodes list on the right
    def append_nodes_right(self, iterable):
        for node in reversed(iterable):
            self.dll.append_node_at(self, node)

# Double Linked list iterator
class DLListIterator:
    def __init__(self, node):
        self.curr = node

    def __iter__(self):
        return self

    def __next__(self):
        if self.curr is None:
            raise StopIteration
        val = self.curr
        self.curr = self.curr.next
        return val

class DLListReverseIterator:
    def __init__(self, node):
        self.curr = node

    def __iter__(self):
        return self

    def __next__(self):
        if self.curr is None:
            raise StopIteration
        val = self.curr
        self.curr = self.curr.prev
        return val

# Double Linked List
class DLList:

    def __init__(self, iterable = None):
        self.head = None
        self.tail = None
        self.len = 0
        if iterable is not None:
            self.join_nodes(iterable)

    # Container functions
    def __iter__(self):
        return DLListIterator(self.head)

    def __reversed__(self):
        return DLListReverseIterator(self.tail)

    def __len__(self):
        # Recalculate each time - optimize
        return self.len

    # Most generic append
    def append_node_at(self, node_at, node):
        if node_at.dll != self:
            raise ValueError("attempting to append at node that is not part of the dllist")
        if node.dll is not None:
            node.dll.remove_node(node)
        if node_at.next is not None:
            node_at.next.prev = node
        else:
            self.tail = node
        node.prev = node_at
        node.next = node_at.next
        node.dll = self
        node_at.next = node
        self.len += 1
        return node

    def append_node_left_of(self, node_at, node):
        if node_at.dll != self:
            raise ValueError("attempting to append at node that is not part of the dllist")
        if node.dll is not None:
            node.dll.remove_node(node)
        if node_at.prev is not None:
            node_at.prev.next = node
        else:
            self.head = node
        node.prev = node_at.prev
        node.next = node_at
        node.dll = self
        node_at.prev = node
        self.len += 1
        return node

    def remove_node(self, node):
        if node.dll != self:
            raise ValueError("attempting to remove node not in list")
        if node.prev is not None:
            node.prev.next = node.next
        else:
            self.head = node.next
        if node.next != None:
            node.next.prev = node.prev
        else:
            self.tail = node.prev
        node.dll = None
        node.prev = None
        node.next = None
        self.len -= 1

    # Compount append functions
    def append_node(self, node):
        if node.dll:
            node.dll.remove_node(node)
        if self.tail is None:
            self.tail = node
            self.head = node
            node.next = None
            node.prev = None
            node.dll = self
            self.len = 1
        else:
            self.append_node_at(self.tail, node)
        return node

    def append_node_left(self, node):
        if node.dll:
            node.dll.remove_node(node)
        if self.head is None:
            self.head = node
            self.tail = node
            node.next = None
            node.prev = None
            node.dll = self
            self.len = 1
        else:
            self.append_node_left_of(self.head, node)
        return node

    # Generic    
    def append_nodes(self, iterable):
        for node in iterable:
            self.append_node(node)

    # Optimized for dlllist
    def append_nodes_dllist(self, dllist):
        # Replace heads and tails
        dllist.head.prev = self.tail
        self.tail.next = dllist.head
        self.tail = dllist.tail
        for node in dllist:
            node.dll = self
        dllist.head = None
        dllist.tail = None

    # Clear
    def clear(self):
        while self.head is not None:
            self.remove_node(self.head)    

# To test
if __name__ == "__main__":
    class ValueNode(Node):
        def __init__(self, v):
            Node.__init__(self)
            self.value = v

        def __str__(self):
            return str(self.value)


    dll1 = DLList()
    n1 = dll1.append_node(ValueNode(1))
    n2 = dll1.append_node(ValueNode(2))
    n3 = dll1.append_node(ValueNode(3))
    n4 = dll1.append_node(ValueNode(4))
    n5 = dll1.append_node(ValueNode(5))
    
    print("- Iterate list")
    for n in dll1:
        print('n : {val}'.format(val = n.value))

    dll1.head.append_node(ValueNode(1.1))

    print("- iterate edited list")
    for n in dll1:
        print('n : {val}'.format(val = n.value))

    print("- insert while iterating")
    for n in dll1:
        if n.value == 3:
            n.append_node_left(ValueNode("pre 3"))
            n.append_node(ValueNode("post 3"))
            n.append_node(ValueNode("post 3.2"))
            dll1.tail.append_node(ValueNode("Tail Appendix"))
        print('n : {val}'.format(val = n.value))


    print(" - delete test")
    for n in dll1:
        print('n : {val}'.format(val = n.value))

    for n in dll1:
        if n.value == 3:
            dll1.remove_node(n)

    print("after delete of n == 3")
    for n in dll1:
        print('n : {val}'.format(val = n.value))
    