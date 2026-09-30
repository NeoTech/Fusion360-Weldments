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
    origin = Point3D(0, 0, 0)
    normal = Vector3D(1, 0, 0)


class FakeConstructionPlane:
    geometry = FakePlaneGeometry()

    def deleteMe(self):
        _record("ConstructionPlane.deleteMe")
        return True


class FakeSketch:
    def __init__(self):
        self.name = ""
        self.transform = Matrix3D()
        self._profiles = _Collection([_Node("profile")])
        self.sketchCurves = _Node("sketch.sketchCurves")

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
        _record("ConstructionPlaneInput.setByPath", dist_type)
        return True


class FakeConstructionPlanes:
    def createInput(self):
        return FakeCPInput()

    def add(self, ci):
        _record("ConstructionPlanes.add")
        return FakeConstructionPlane()


class FakeSketches:
    def add(self, plane):
        _record("Sketches.add")
        return FakeSketch()


class FakeExtrudeInput:
    def __init__(self):
        self.startExtent = None

    def setOneSideExtent(self, extent, direction):
        _record("ExtrudeFeatureInput.setOneSideExtent", direction)
        return True


class FakeExtrudeFeatures:
    def createInput(self, profile, operation):
        _record("ExtrudeFeatures.createInput", operation)
        return FakeExtrudeInput()

    def add(self, ei):
        _record("ExtrudeFeatures.add")
        return _Node("feature")


class FakeFeatures:
    extrudeFeatures = FakeExtrudeFeatures()


class FakeRoot:
    def __init__(self):
        self.constructionPlanes = FakeConstructionPlanes()
        self.sketches = FakeSketches()
        self.features = FakeFeatures()


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
    core.LogLevels = _Enum(InfoLogLevel=0)

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
