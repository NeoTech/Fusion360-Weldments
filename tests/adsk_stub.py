"""A minimal fake ``adsk`` package so the command layer can be imported and
unit-tested outside of Fusion.

Import this module (or call :func:`install`) *before* importing anything from
``commands``.  It records every API call on module-level lists so tests can
assert the command drives the Fusion API the way we expect (correct enums,
chaining off, proportional distance 0, to-entity = line end point, ...).
"""

import sys
import types

# --- call recorder ---------------------------------------------------------- #
CALLS = []


def reset():
    del CALLS[:]


def _record(name, *args):
    CALLS.append((name, args))


# --- generic recording object ---------------------------------------------- #
class _Node:
    """A chainable recording object; attribute access returns more _Nodes."""

    def __init__(self, path="root"):
        self._path = path

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return _Node(f"{self._path}.{item}")

    def __call__(self, *args):
        _record(self._path, *args)
        return _Node(self._path + "()")


class Point3D:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z
        self._recorded = False

    @staticmethod
    def create(x=0.0, y=0.0, z=0.0):
        return Point3D(x, y, z)

    def transformBy(self, mat):
        _record("Point3D.transformBy", mat)
        return True


class Vector3D:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    @staticmethod
    def create(x=0.0, y=0.0, z=0.0):
        return Vector3D(x, y, z)

    def normalize(self):
        _record("Vector3D.normalize")

    def dotProduct(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    def crossProduct(self, other):
        return Vector3D(self.y * other.z - self.z * other.y,
                        self.z * other.x - self.x * other.z,
                        self.x * other.y - self.y * other.x)


class Matrix3D:
    def __init__(self):
        self._inv = False

    @staticmethod
    def create():
        return Matrix3D()

    def copy(self):
        return Matrix3D()

    def invert(self):
        _record("Matrix3D.invert")

    def asArray(self):
        return (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


class ValueInput:
    def __init__(self, v):
        self.value = v

    @staticmethod
    def createByReal(v):
        _record("ValueInput.createByReal", v)
        return ValueInput(v)

    @staticmethod
    def createByString(s):
        return ValueInput(s)


# --- enums (just need stable, distinguishable values) ---------------------- #
class _Enum:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _make_enums():
    fusion = sys.modules["adsk.fusion"]
    fusion.ChainedCurveOptions = _Enum(
        noChainedCurves=0, connectedChainedCurves=1, tangentChainedCurves=2)
    fusion.PathDistanceTypes = _Enum(
        ProportionalPathDistanceType=0, PhysicalPathDistanceType=1)
    fusion.ExtentDirections = _Enum(
        PositiveExtentDirection=0, NegativeExtentDirection=1, SymmetricExtentDirection=2)
    fusion.FeatureOperations = _Enum(
        JoinFeatureOperation=0, CutFeatureOperation=1,
        IntersectFeatureOperation=2, NewBodyFeatureOperation=3)
    fusion.PointContainment = _Enum(
        PointInsidePointContainment=0, PointOutsidePointContainment=1,
        PointOnPointContainment=2)


# --- the objects the command touches ---------------------------------------- #
class _Geometry:
    def __init__(self, start, end):
        self.startPoint = Point3D(*start)
        self.endPoint = Point3D(*end)


class FakeLine:
    def __init__(self, start=(0, 0, 0), end=(10, 0, 0)):
        self._wg = _Geometry(start, end)
        self.endSketchPoint = _Node("line.endSketchPoint")

    @property
    def worldGeometry(self):
        return self._wg

    @property
    def length(self):
        dx = self._wg.endPoint.x - self._wg.startPoint.x
        dy = self._wg.endPoint.y - self._wg.startPoint.y
        dz = self._wg.endPoint.z - self._wg.startPoint.z
        return (dx * dx + dy * dy + dz * dz) ** 0.5


class FakePlaneGeometry:
    def __init__(self, normal=(1, 0, 0)):
        self.origin = Point3D(0, 0, 0)
        self.normal = Vector3D(*normal)


class FakeConstructionPlane:
    def __init__(self, normal=(1, 0, 0)):
        self.geometry = FakePlaneGeometry(normal)

    def deleteMe(self):
        _record("ConstructionPlane.deleteMe")
        return True


class FakeSketchLines:
    """Records added lines and supports count/item (needed for a revolve axis)."""

    def __init__(self):
        self._lines = []

    def addByTwoPoints(self, p1, p2):
        _record("SketchLines.addByTwoPoints", p1, p2)
        node = _Node("sketchLine")
        self._lines.append(node)
        return node

    @property
    def count(self):
        return len(self._lines)

    def item(self, i):
        return self._lines[i]


class FakeSketchCurves:
    def __init__(self):
        self.sketchLines = FakeSketchLines()
        self.sketchArcs = _Node("sketch.sketchCurves.sketchArcs")
        self.sketchCircles = _Node("sketch.sketchCurves.sketchCircles")


class FakeSketch:
    def __init__(self):
        self.name = ""
        self.transform = Matrix3D()
        self._profiles = _Collection([_Node("profile")])
        self.sketchCurves = FakeSketchCurves()

    def deleteMe(self):
        _record("Sketch.deleteMe")
        return True

    @property
    def profiles(self):
        return self._profiles


class _Collection:
    def __init__(self, items):
        self._items = items

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakePath:
    @staticmethod
    def create(curves, chain):
        _record("Path.create", chain)
        return _Node("path")


class FakeCPInput:
    def setByPath(self, path, dist_type, dist):
        _record("ConstructionPlaneInput.setByPath", dist_type, dist)
        return True

    def setByTwoEdges(self, l1, l2):
        _record("ConstructionPlaneInput.setByTwoEdges")
        return True


class FakeConstructionPlanes:
    def __init__(self, root=None):
        self._root = root

    def createInput(self):
        return FakeCPInput()

    def add(self, ci):
        _record("ConstructionPlanes.add")
        plane = FakeConstructionPlane()
        if self._root is not None:
            plane._root = self._root
            self._root._planes.append(plane)
        return plane

    @property
    def count(self):
        return len(self._root._planes) if self._root else 0

    def item(self, i):
        return self._root._planes[i]


class FakeSketches:
    def add(self, plane):
        _record("Sketches.add")
        sk = FakeSketch()
        sk.attachedPlane = plane
        return sk


class FakeExtrudeInput:
    def __init__(self):
        self.startExtent = None

    def setOneSideExtent(self, extent, direction):
        _record("ExtrudeFeatureInput.setOneSideExtent", direction)
        return True

    def setDistanceExtent(self, symmetric, value):
        _record("ExtrudeFeatureInput.setDistanceExtent", symmetric, value)
        return True


class FakeExtrudeFeatures:
    def __init__(self, root=None):
        self._root = root

    def createInput(self, profile, operation):
        _record("ExtrudeFeatures.createInput", operation)
        return FakeExtrudeInput()

    def add(self, ei):
        _record("ExtrudeFeatures.add")
        feat = FakeFeature("adsk::fusion::ExtrudeFeature", self._root,
                           [FakeBody()])
        if self._root is not None:
            self._root.features._items.append(feat)
        return feat


class FakeRevolveInput:
    def setAngleExtent(self, is_symmetric, angle):
        _record("RevolveFeatureInput.setAngleExtent", is_symmetric, angle)
        return True


class FakeRevolveFeatures:
    def __init__(self, root=None):
        self._root = root

    def createInput(self, profile, axis, operation):
        _record("RevolveFeatures.createInput", operation)
        return FakeRevolveInput()

    def add(self, ri):
        _record("RevolveFeatures.add")
        feat = FakeFeature("adsk::fusion::RevolveFeature", self._root,
                           [FakeBody()])
        if self._root is not None:
            self._root.features._items.append(feat)
        return feat


# --- bodies, features, and boolean/split operations ------------------------ #
class FakeBoundingBox:
    def __init__(self, center=(0.0, 0.0, 0.0), half=1.0):
        self.minPoint = Point3D(center[0] - half, center[1] - half,
                                center[2] - half)
        self.maxPoint = Point3D(center[0] + half, center[1] + half,
                                center[2] + half)


class FakeBody:
    """A body with a real centroid (``center``) and a scripted pointContainment.

    ``center`` drives ``boundingBox`` so the corner-cut code can tell fragments
    apart by signed distance to the miter plane; ``contains`` still drives
    ``pointContainment`` for any code that uses it.
    """

    def __init__(self, contains=True, name="Body", center=(0.0, 0.0, 0.0)):
        self._contains = contains
        self.name = name
        self.opacity = 1.0
        self._center = center
        self.boundingBox = FakeBoundingBox(center)
        self._owner = None

    def pointContainment(self, pt):
        _record("Body.pointContainment")
        import sys as _sys
        pc = _sys.modules["adsk.fusion"].PointContainment
        return (pc.PointInsidePointContainment if self._contains
                else pc.PointOutsidePointContainment)

    def deleteMe(self):
        _record("Body.deleteMe")
        if self._owner is not None and self in self._owner._items:
            self._owner._items.remove(self)
        return True


class FakeBodies:
    def __init__(self, items=None):
        self._items = list(items or [])
        for it in self._items:
            it._owner = self

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def append(self, b):
        b._owner = self
        self._items.append(b)


class FakeFeature:
    def __init__(self, object_type, root, bodies=None):
        self.objectType = object_type
        self._root = root
        self.bodies = FakeBodies(bodies)

    def deleteMe(self):
        _record("Feature.deleteMe", self.objectType)
        if self._root is not None and self in self._root.features._items:
            self._root.features._items.remove(self)
        return True


class _FakeCollection:
    """A list-backed count/item collection (features, planes, ...)."""

    def __init__(self, items=None):
        self._items = list(items or [])

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeSplitInput:
    def __init__(self, body, tool, gap):
        self.body = body
        self.tool = tool
        self.distanceGap = gap


class FakeSplitBodyFeatures:
    def __init__(self, root=None):
        self._root = root

    def createInput(self, body, tool, gap):
        _record("SplitBodyFeatures.createInput", body, tool, gap)
        return FakeSplitInput(body, tool, gap)

    def add(self, si):
        _record("SplitBodyFeatures.add")
        # Model: the kept half stays on the member feature; the split feature
        # lists both halves, the second being the deletable waste (placed far
        # from the kept body's centroid so signed distance flags it as waste).
        feat = FakeFeature("adsk::fusion::SplitBodyFeature", self._root,
                           [si.body, FakeBody(contains=False,
                                             center=(1e6, 2e6, 3e6))])
        if self._root is not None:
            self._root.features._items.append(feat)
        return None  # real API returns None in parametric designs


class FakeCombineInput:
    def __init__(self, target, tools):
        self.target = target
        self.tools = tools
        self.operation = None
        self.isKeepToolBodies = False


class FakeCombineFeatures:
    def __init__(self, root=None):
        self._root = root

    def createInput(self, target, tools):
        _record("CombineFeatures.createInput", target, tools)
        return FakeCombineInput(target, tools)

    def add(self, ci):
        _record("CombineFeatures.add", ci.operation, ci.isKeepToolBodies)
        feat = FakeFeature("adsk::fusion::CombineFeature", self._root,
                           [ci.target])
        if not ci.isKeepToolBodies:
            # Real API consumes the tool bodies: their owning feature loses
            # them and becomes empty (the waste-prism extrude in a miter cut).
            for f in list(self._root.features._items):
                for b in list(f.bodies._items):
                    if any(b is t for t in ci.tools._items):
                        f.bodies._items.remove(b)
        if self._root is not None:
            self._root.features._items.append(feat)
        return feat


class FakeFeatures(_FakeCollection):
    def __init__(self, root=None):
        super().__init__()
        self.extrudeFeatures = FakeExtrudeFeatures(root)
        self.revolveFeatures = FakeRevolveFeatures(root)
        self.splitBodyFeatures = FakeSplitBodyFeatures(root)
        self.combineFeatures = FakeCombineFeatures(root)


class FakeObjectCollection:
    def __init__(self):
        self._items = []

    @classmethod
    def create(cls):
        _record("ObjectCollection.create")
        return cls()

    def add(self, obj):
        self._items.append(obj)
        return True

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeRoot:
    def __init__(self):
        self.features = FakeFeatures(self)
        self._planes = []
        self.constructionPlanes = FakeConstructionPlanes(self)
        self.sketches = FakeSketches()
        self.xYConstructionPlane = FakeConstructionPlane((0, 0, 1))


# --- command-input fakes (for driving the dialog event handlers) ----------- #
class FakeInput:
    """A generic value input: angle/distance/text/bool all share this shape."""

    def __init__(self, id, kind, value=0.0):
        self.id = id
        self.kind = kind
        self._value = value
        self.isVisible = True
        self.isEnabled = True
        self.hasMinimumValue = True
        self.hasMaximumValue = True
        self.manipulatorOrigin = None
        self.manipulatorCount = 0

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = v
        _record("input.value.set", self.id, v)

    def setManipulator(self, *args):
        _record("input.setManipulator", self.id, *args)
        self.manipulatorOrigin = args[0]
        self.manipulatorCount += 1
        return True


class FakeListItem:
    """A dropdown list item whose isSelected is exclusive among siblings,
    mirroring Fusion's ListItem behaviour (selecting one deselects the rest)."""

    def __init__(self, parent, name, isSelected=False):
        self._parent = parent
        self.name = name
        self.id = f"_li{id(parent)}_{name}"
        self.kind = "li"
        self._selected = isSelected
        if isSelected:
            self._select_only_me()

    def _select_only_me(self):
        for it in self._parent._items:
            it._selected = (it is self)

    @property
    def isSelected(self):
        return self._selected

    @isSelected.setter
    def isSelected(self, v):
        self._selected = bool(v)
        if v:
            self._select_only_me()


class FakeListItems:
    def __init__(self):
        self._items = []

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def add(self, name, isSelected=False, subItem=""):
        item = FakeListItem(self, name, isSelected)
        self._items.append(item)
        return item

    def clear(self):
        self._items = []


class FakeDropDown(FakeInput):
    def __init__(self, id):
        super().__init__(id, "dropdown", 0)
        self.listItems = FakeListItems()


class FakeSelectionItem:
    def __init__(self, entity):
        self.entity = entity


class FakeSelection(FakeInput):
    def __init__(self, id, parent=None):
        super().__init__(id, "selection", 0)
        self._ents = []
        self.parentCommand = parent

    @property
    def selectionCount(self):
        return len(self._ents)

    def selection(self, i):
        return FakeSelectionItem(self._ents[i])

    def addSelection(self, entity):
        self._ents.append(entity)
        return True

    def clearSelection(self):
        self._ents = []

    def addSelectionFilter(self, kind):
        return True

    def setSelectionLimits(self, minc, maxc):
        return True


class FakeTable:
    def __init__(self, id, ncols, ratio):
        self.id = id
        self.numberOfColumns = ncols
        self.ratio = ratio
        self._rows = 0
        self._cells = {}
        self._toolbar = []

    def addCommandInput(self, inp, row, col, rowSpan=0, columnSpan=0):
        _record("table.addCommandInput", inp.id, row, col)
        self._cells[(row, col)] = inp
        self._rows = max(self._rows, row + 1)
        return True

    def addToolbarCommandInput(self, inp):
        _record("table.addToolbarCommandInput", inp.id)
        self._toolbar.append(inp)
        return True

    def deleteRow(self, row):
        _record("table.deleteRow", row)
        for (r, c) in list(self._cells):
            if r == row:
                del self._cells[(r, c)]
        self._rows = max(0, self._rows - 1)
        return True

    @property
    def rowCount(self):
        return self._rows

    def getPosition(self, inp):
        for (r, c), v in self._cells.items():
            if v is inp:
                return (True, r, c, 0, 0)
        return (False, -1, -1, 0, 0)


class FakeCommand:
    def __init__(self):
        self.commandInputs = FakeCommandInputs(self)


class FakeCommandInputs:
    def __init__(self, command=None):
        self._by_id = {}
        self._command = command

    def _add(self, inp):
        self._by_id[inp.id] = inp
        return inp

    def itemById(self, id):
        return self._by_id.get(id)

    def addSelectionInput(self, id, name, desc):
        return self._add(FakeSelection(id, self._command))

    def addDropDownCommandInput(self, id, name, style):
        return self._add(FakeDropDown(id))

    def addTableCommandInput(self, id, name, ncols, ratio):
        return self._add(FakeTable(id, ncols, ratio))

    def addBoolCommandInput(self, id, name, value):
        return self._add(FakeInput(id, "bool", value))

    def addBoolValueInput(self, id, name, isCheckBox, resourceFolder, initialValue):
        return self._add(FakeInput(id, "bool", initialValue))

    def addTextBoxCommandInput(self, id, name, text, nlines, readonly):
        return self._add(FakeInput(id, "text", text))

    def addAngleValueCommandInput(self, id, name, vi):
        return self._add(FakeInput(id, "angle", 0.0))

    def addFloatSpinnerCommandInput(self, id, name, unit, mn, mx, step, init):
        # A spinner is a plain editable value box with NO canvas manipulator.
        return self._add(FakeInput(id, "spinner", 0.0))

    def addDistanceValueCommandInput(self, id, name, vi):
        return self._add(FakeInput(id, "distance", 0.0))


# --- install into sys.modules ---------------------------------------------- #
def install():
    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")

    core.Point3D = Point3D
    core.Vector3D = Vector3D
    core.Matrix3D = Matrix3D
    core.ValueInput = ValueInput
    core.Application = _Node("Application")
    core.DropDownStyles = _Enum(TextListDropDownStyle=0)
    core.LogLevels = _Enum(InfoLogLevel=0, ErrorLogLevel=1, WarningLogLevel=2)
    core.ObjectCollection = FakeObjectCollection

    fusion.Path = FakePath
    fusion.ToEntityExtentDefinition = _Node("ToEntityExtentDefinition")
    fusion.DistanceExtentDefinition = _Node("DistanceExtentDefinition")
    fusion.ProfilePlaneStartDefinition = _Node("ProfilePlaneStartDefinition")
    fusion.Design = _Node("Design")

    adsk.core = core
    adsk.fusion = fusion
    sys.modules["adsk"] = adsk
    sys.modules["adsk.core"] = core
    sys.modules["adsk.fusion"] = fusion

    # Any attribute not explicitly defined (e.g. adsk.core.Event used in type
    # annotations across fusionAddInUtils) auto-resolves to a recording node.
    def _module_getattr(name):
        return _Node(f"adsk.{name}")

    core.__getattr__ = _module_getattr
    fusion.__getattr__ = _module_getattr

    _make_enums()
