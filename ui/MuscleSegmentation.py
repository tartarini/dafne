#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Oct 16 15:14:33 2020

@author: francesco

"""
import matplotlib

matplotlib.use("Qt5Agg")

import sys, os, time, math

# print(sys.path)
SCRIPT_DIR = os.path.dirname(os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(__file__))))
sys.path.append(os.path.normpath(SCRIPT_DIR))

from ui.ToolboxWindow import ToolboxWindow
from .pyDicomView import ImageShow
from utils.mask_utils import calc_dice_score, save_npy_masks, save_npz_masks, save_dicom_masks, save_nifti_masks
import matplotlib.pyplot as plt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
import shutil
from datetime import datetime
from .ROIManager import ROIManager

from matplotlib.patches import Circle
import numpy as np
import scipy.ndimage as ndimage
import pickle
import os.path
from collections import deque
import functools

from .BrushPatches import SquareBrush, PixelatedCircleBrush

try:
    import SimpleITK as sitk # this requires simpleelastix! It is NOT available through PIP
except:
    pass

import re
import subprocess

from utils.dicomUtils import load3dDicom, save3dDicom

if os.name == 'posix':
    def checkCapsLock():
        return (int(subprocess.check_output('xset q | grep LED', shell=True)[65]) & 1) == 1
elif os.name == 'nt':
    import ctypes

    hllDll = ctypes.WinDLL("User32.dll")


    def checkCapsLock():
        return ((hllDll.GetKeyState(0x14) & 1) == 1)
else:
    def checkCapsLock():
        return False

try:
    QString("")
except:
    def QString(s):
        return s

ROI_CIRCLE_SIZE = 2
SIMPLIFIED_ROI_POINTS = 20
SIMPLIFIED_ROI_SPACING = 15
ROI_COLOR_ORIG = (1, 0, 0, 0.5)  # red with 0.5 opacity
ROI_SAME_COLOR_ORIG = (1, 1, 0, 0.5)  # yellow with 0.5 opacity
ROI_OTHER_COLOR_ORIG = (0, 0, 1, 0.4)

ROI_COLOR_WACOM = (1, 0, 0, 1)  # red with 1 opacity
ROI_SAME_COLOR_WACOM = (1, 1, 0, 1)  # yellow with 0.5 opacity
ROI_OTHER_COLOR_WACOM = (0, 0, 1, 0.8)

ROI_COLOR = ROI_COLOR_ORIG
ROI_SAME_COLOR = ROI_SAME_COLOR_ORIG
ROI_OTHER_COLOR = ROI_OTHER_COLOR_ORIG

BRUSH_PAINT_COLOR = (1, 0, 0, 0.6)
BRUSH_ERASE_COLOR = (0, 0, 1, 0.6)


ROI_FILENAME = 'rois.p'
AUTOSAVE_INTERVAL = 30

HIDE_ROIS_RIGHTCLICK = True

COLORS = ['blue', 'red', 'green', 'yellow', 'magenta', 'cyan', 'indigo', 'white', 'grey']

HISTORY_LENGTH = 20

MASK_LAYER_COLORMAP = matplotlib.colors.ListedColormap(np.array([
    [0,0,0,0],
    [*ROI_COLOR[:3],1]
]))

MASK_LAYER_OTHER_COLORMAP = matplotlib.colors.ListedColormap(np.array([
    [0,0,0,0],
    [*ROI_OTHER_COLOR[:3],1]
]))


MASK_LAYER_ALPHA = 0.4

# define a circle with a contains method that for some reason does not work with conventional circles
class MyCircle(Circle):
    def __init__(self, xy, *args, **kwargs):
        Circle.__init__(self, xy, *args, **kwargs)
        self.xy = xy

    def contains(self, event):
        return ((event.xdata - self.xy[0]) ** 2 + (event.ydata - self.xy[1]) ** 2) < self.get_radius() ** 2


def snapshotSaver(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self.saveSnapshot()
        func(self, *args, **kwargs)

    return wrapper


class MuscleSegmentation(ImageShow, QObject):

    undo_possible = pyqtSignal(bool)
    redo_possible = pyqtSignal(bool)

    def __init__(self, *args, **kwargs):
        ImageShow.__init__(self, *args, **kwargs)
        QObject.__init__(self)
        self.fig.canvas.mpl_connect('close_event', self.closeCB)
        # self.instructions = "Shift+click: add point, Shift+dblclick: optimize/simplify, Ctrl+click: remove point, Ctrl+dblclick: delete ROI, n: propagate fw, b: propagate back"
        self.setupToolbar()
        self.roiStack = None
        self.transforms = {}
        self.invtransforms = {}
        #self.allROIs = {}  # allROIs is dict[roiName: dict[imageNumber: (subroi)list[Splines]]]
        self.roiManager = None
        self.wacom = False
        self.roiColor = ROI_COLOR
        self.roiOther = ROI_OTHER_COLOR
        self.roiSame = ROI_SAME_COLOR
        self.saveDicom = False

        self.model_provider = None
        self.dl_classifier = None
        self.dl_segmenters = {}
        self.classifications = []

        # self.fig.canvas.setCursor(Qt.BlankCursor)
        self.app = None

        # self.setCmap('viridis')
        self.extraOutputParams = []
        self.transformsChanged = False

        self.lastsave = datetime.now()
        self.hideRois = False
        self.history = deque(maxlen=HISTORY_LENGTH)
        self.currentHistoryPoint = 0

        self.originalSegmentationMasks = {}
        self.brush_patch = None
        self.maskImPlot = None
        self.maskOtherImPlot = None
        self.activeMask = None
        self.otherMask = None
        self.roiChanged = {}

        self.editMode = ToolboxWindow.EDITMODE_MASK


    # def toggleWacom(self, wacomState = None):
    #     if wacomState is not None: self.wacom = not wacomState # force a toggle
    #     if self.wacom:
    #         self.wacom = False
    #         self.roiColor = ROI_COLOR_ORIG
    #         self.roiOther = ROI_OTHER_COLOR_ORIG
    #     else:
    #         self.wacom = True
    #         self.roiColor = ROI_COLOR_WACOM
    #         self.roiOther = ROI_OTHER_COLOR_WACOM
    #     self.wacomAction.setChecked(self.wacom)
    #     #self.redraw()
    #     self.redraw()

    #############################################################################################
    ###
    ### Toolbar interaction
    ###
    ##############################################################################################

    def setupToolbar(self):

        if 'Elastix' in dir(sitk):
            showRegistrationGui = True
        else:
            print("Elastix is not available")
            showRegistrationGui = False


        self.toolbox_window = ToolboxWindow(activate_registration=showRegistrationGui)
        self.toolbox_window.show()

        self.toolbox_window.editmode_changed.connect(self.changeEditMode)

        self.toolbox_window.roi_added.connect(self.addRoi)
        self.toolbox_window.subroi_added.connect(self.addSubRoi)

        self.toolbox_window.roi_deleted.connect(self.removeRoi)
        self.toolbox_window.subroi_deleted.connect(self.removeSubRoi)

        self.toolbox_window.roi_changed.connect(self.changeRoi)

        self.toolbox_window.roi_clear.connect(self.clearCurrentROI)

        self.toolbox_window.do_autosegment.connect(self.doSegmentation)

        self.toolbox_window.undo.connect(self.undo)
        self.toolbox_window.redo.connect(self.redo)
        self.undo_possible.connect(self.toolbox_window.undo_enable)
        self.redo_possible.connect(self.toolbox_window.redo_enable)

        self.toolbox_window.contour_simplify.connect(self.simplify)
        self.toolbox_window.contour_optimize.connect(self.optimize)

        self.toolbox_window.calculate_transforms.connect(self.calcTransforms)
        self.toolbox_window.contour_propagate_fw.connect(self.propagate)
        self.toolbox_window.contour_propagate_bw.connect(self.propagateBack)

        self.toolbox_window.roi_import.connect(self.loadROIPickle)
        self.toolbox_window.roi_export.connect(self.saveROIPickle)

        self.toolbox_window.data_open.connect(self.loadDirectory)

        self.toolbox_window.masks_export.connect(self.saveResults)


    @pyqtSlot(str)
    def changeEditMode(self, mode):
        print("Changing edit mode")
        self.editMode = mode
        roi_name = self.getCurrentROIName()
        if mode == ToolboxWindow.EDITMODE_MASK:
            self.removeContours()
            self.updateMasksFromROIs()
        else:
            self.removeMasks()
        self.updateRoiList()
        self.toolbox_window.set_current_roi(roi_name)
        self.redraw()

    def setState(self, state):
        self.state = state

    def getState(self):
        if self.toolbox_window.valid_roi(): return 'MUSCLE'
        return 'INACTIVE'

    def updateRoiList(self):
        if not self.roiManager: return
        roiDict = {}
        imageN = int(self.curImage)
        for roiName in self.roiManager.get_roi_names():
            if self.editMode == ToolboxWindow.EDITMODE_MASK:
                if not self.roiManager.contains(roiName, imageN):
                    self.roiManager.add_mask(roiName, imageN)
                n_subrois = 1
            else:
                if not self.roiManager.contains(roiName, imageN) or self.roiManager.get_roi_mask_pair(roiName,
                                                                                                      imageN).get_subroi_len() == 0:
                    self.addSubRoi(roiName, imageN)
                n_subrois = self.roiManager.get_roi_mask_pair(roiName, imageN).get_subroi_len()
            roiDict[roiName] = n_subrois  # dict: roiname -> n subrois per slice
        self.toolbox_window.set_rois_list(roiDict)

    #############################################################################################
    ###
    ### History
    ###
    #############################################################################################

    def saveSnapshot(self):
        # clear history until the current point, so we can't redo anymore
        print("Saving snapshot")
        while self.currentHistoryPoint > 0:
            self.history.popleft()
            self.currentHistoryPoint -= 1
        self.history.appendleft(pickle.dumps(self.roiManager))
        self.undo_possible.emit(self.canUndo())
        self.redo_possible.emit(self.canRedo())

    def canUndo(self):
        return self.currentHistoryPoint < len(self.history) - 1

    def canRedo(self):
        return self.currentHistoryPoint > 0

    def _changeHistory(self):
        print(self.currentHistoryPoint, len(self.history))
        roiName = self.getCurrentROIName()
        subRoiNumber = self.getCurrentSubroiNumber()
        self.clearAllROIs()
        self.roiManager = pickle.loads(self.history[self.currentHistoryPoint])
        self.updateRoiList()
        if self.roiManager.contains(roiName):
            #TODO: mask-aware
            if subRoiNumber < self.roiManager.get_roi_mask_pair(roiName, self.curImage).get_subroi_len():
                self.toolbox_window.set_current_roi(roiName, subRoiNumber)
            else:
                self.toolbox_window.set_current_roi(roiName, 0)
        self.redraw()
        self.undo_possible.emit(self.canUndo())
        self.redo_possible.emit(self.canRedo())

    @pyqtSlot()
    def undo(self):
        if not self.canUndo(): return
        if self.currentHistoryPoint == 0:
            self.saveSnapshot()  # push current status into the history for redo
        self.currentHistoryPoint += 1
        self._changeHistory()

    @pyqtSlot()
    def redo(self):
        if not self.canRedo(): return
        self.currentHistoryPoint -= 1
        self._changeHistory()
        if self.currentHistoryPoint == 0:
            self.history.popleft()  # remove current status from the history

    ############################################################################################################
    ###
    ### ROI management
    ###
    #############################################################################################################

    def getRoiFileName(self):
        if self.basename:
            roi_fname = self.basename + '.' + ROI_FILENAME
        else:
            roi_fname = ROI_FILENAME
        return os.path.join(self.basepath, roi_fname)

    def clearAllROIs(self):
        self.roiManager.clear()
        self.updateRoiList()
        self.redraw()

    def clearSubrois(self, name, sliceN):
        self.roiManager.clear(name, sliceN)
        self.updateRoiList()
        self.redraw()

    @pyqtSlot(str)
    @snapshotSaver
    def removeRoi(self, roi_name):
        print("RemoveRoi")
        print(self.roiManager.get_roi_names())
        self.roiManager.clear(roi_name)
        self.updateRoiList()
        self.redraw()

    @pyqtSlot(int)
    @snapshotSaver
    def removeSubRoi(self, subroi_number):
        current_name, _ = self.toolbox_window.get_current_roi_subroi()
        self.roiManager.clear_subroi(current_name, int(self.curImage), subroi_number)
        self.updateRoiList()
        self.redraw()

    @pyqtSlot(str)
    @snapshotSaver
    def addRoi(self, roiName):
        if self.editMode == ToolboxWindow.EDITMODE_MASK:
            self.roiManager.add_mask(roiName, int(self.curImage))
        else:
            self.roiManager.add_roi(roiName, int(self.curImage))
        self.updateRoiList()
        self.toolbox_window.set_current_roi(roiName, 0)
        self.redraw()

    @pyqtSlot()
    @snapshotSaver
    def addSubRoi(self, roi_name=None, imageN=None):
        if not roi_name:
            roi_name, _ = self.toolbox_window.get_current_roi_subroi()
        if imageN is None:
            imageN = int(self.curImage)
        self.roiManager.add_subroi(roi_name, imageN)
        self.updateRoiList()
        self.toolbox_window.set_current_roi(roi_name, self.roiManager.get_roi_mask_pair(roi_name,
                                                                                        imageN).get_subroi_len() - 1)
        self.redraw()

    @pyqtSlot(str, int)
    def changeRoi(self, roi_name, subroi_index):
        print(roi_name, subroi_index)
        self.redraw()

    #########################################################################################
    ###
    ### ROI modifications
    ###
    #########################################################################################

    def getInverseTransform(self, imIndex):
        try:
            return self.invtransforms[imIndex]
        except KeyError:
            self.calcInverseTransform(imIndex)
            return self.invtransforms[imIndex]

    def getTransform(self, imIndex):
        try:
            return self.transforms[imIndex]
        except KeyError:
            self.calcTransform(imIndex)
            return self.transforms[imIndex]

    def calcTransform(self, imIndex):
        if imIndex >= len(self.imList) - 1: return
        fixedImage = self.imList[imIndex]
        movingImage = self.imList[imIndex + 1]
        self.transforms[imIndex] = self.runElastix(fixedImage, movingImage)
        self.transformsChanged = True

    def calcInverseTransform(self, imIndex):
        if imIndex < 1: return
        fixedImage = self.imList[imIndex]
        movingImage = self.imList[imIndex - 1]
        self.invtransforms[imIndex] = self.runElastix(fixedImage, movingImage)
        self.transformsChanged = True

    def runElastix(self, fixedImage, movingImage):
        elastixImageFilter = sitk.ElastixImageFilter()
        elastixImageFilter.SetLogToConsole(False)
        elastixImageFilter.SetLogToFile(False)

        elastixImageFilter.SetFixedImage(sitk.GetImageFromArray(fixedImage))
        elastixImageFilter.SetMovingImage(sitk.GetImageFromArray(movingImage))
        print("Registering...")

        elastixImageFilter.Execute()
        print("Done")
        return elastixImageFilter.GetTransformParameterMap()

    def calcTransforms(self):
        qbar = QProgressBar()
        qbar.setRange(0, len(self.imList) - 1)
        qbar.setWindowTitle(QString("Registering images"))
        qbar.setWindowModality(Qt.ApplicationModal)
        qbar.move(800, 500)
        qbar.show()

        for imIndex in range(len(self.imList)):
            qbar.setValue(imIndex)
            plt.pause(.000001)
            print("Calculating image:", imIndex)
            # the transform was already calculated
            if imIndex not in self.transforms:
                self.calcTransform(imIndex)
            if imIndex not in self.invtransforms:
                self.calcInverseTransform(imIndex)

        qbar.close()
        print("Saving transforms")
        self.pickleTransforms()

    def propagateAll(self):
        while self.curImage < len(self.imList) - 1:
            self.propagate()
            plt.pause(.000001)

    def propagateBackAll(self):
        while self.curImage > 0:
            self.propagateBack()
            plt.pause(.000001)

    @snapshotSaver
    def simplify(self):
        r = self.getCurrentROI()
        # self.setCurrentROI(r.getSimplifiedSpline(SIMPLIFIED_ROI_POINTS))
        # self.setCurrentROI(r.getSimplifiedSpline(spacing=SIMPLIFIED_ROI_SPACING))
        self.setCurrentROI(r.getSimplifiedSpline3())
        # self.redraw()
        self.redraw()

    @snapshotSaver
    def optimize(self):
        print("Optimizing ROI")
        r = self.getCurrentROI()
        center = r.getCenterOfMass()
        if center is None:
            print("No roi to optimize!")
            return

        newKnots = []
        for index, knot in enumerate(r.knots):
            # newKnot = self.optimizeKnot(center, knot)
            # newKnot = self.optimizeKnot2(knot, r.getKnot(index-1), r.getKnot(index+1))
            newKnot = self.optimizeKnot3(r, index)
            # newKnot = self.optimizeKnotDL(knot)
            newKnots.append(newKnot)

        for index, knot in enumerate(r.knots):
            r.replaceKnot(index, newKnots[index])
        # self.redraw()
        self.redraw()

    # optimizes a knot along an (approximatE) normal to the curve
    def optimizeKnot2(self, knot, prevKnot, nextKnot):

        print("optimizeKnot2")

        optim_region = 5
        optim_region_points = optim_region * 4  # subpixel resolution

        # special case vertical line
        if prevKnot[0] == nextKnot[0]:
            # optimize along a horizontal line
            ypoints = knot[1] * np.ones((2 * optim_region_points))

            # define inside/outside
            if knot[0] < prevKnot[0]:
                xpoints = np.linspace(knot[0] + optim_region, knot[0] - optim_region, 2 * optim_region_points)
            else:
                xpoints = np.linspace(knot[0] - optim_region, knot[0] + optim_region, 2 * optim_region_points)
            z = ndimage.map_coordinates(self.image, np.vstack((ypoints, xpoints))).astype(np.float32)
        elif prevKnot[1] == nextKnot[1]:  # special case horizontal line
            # optimize along a horizontal line
            xpoints = knot[0] * np.ones((2 * optim_region_points))
            if knot[1] < prevKnot[1]:
                ypoints = np.linspace(knot[1] + optim_region, knot[1] - optim_region, 2 * optim_region_points)
            else:
                ypoints = np.linspace(knot[1] - optim_region, knot[1] + optim_region, 2 * optim_region_points)
            z = ndimage.map_coordinates(self.image, np.vstack((ypoints, xpoints))).astype(np.float32)
        else:
            slope = (nextKnot[1] - prevKnot[1]) / (nextKnot[0] - prevKnot[0])
            slope_perpendicular = -1 / slope
            x_dist = np.sqrt(optim_region / (
                    slope_perpendicular ** 2 + 1))  # solving the system (y1-y0) = m(x1-x0) and (y1-y0)^2 + (x1-x0)^2 = d

            # define inside*outside perimeter. Check line intersection. Is this happening on the right or on the left of the point? Right: go from high x to low x
            # x_intersection = (slope_perpendicular*knot[0] - knot[1] - slope*prevKnot[0] + prevKnot[1])/(slope_perpendicular-slope)
            # print knot[0]
            # print x_intersection
            # if x_intersection > knot[0]: x_dist = -x_dist

            x_min = knot[0] - x_dist
            x_max = knot[0] + x_dist
            y_min = knot[1] - slope_perpendicular * x_dist
            y_max = knot[1] + slope_perpendicular * x_dist
            xpoints = np.linspace(x_min, x_max, 2 * optim_region_points)
            ypoints = np.linspace(y_min, y_max, 2 * optim_region_points)
            z = ndimage.map_coordinates(self.image, np.vstack((ypoints, xpoints))).astype(np.float32)
        diffz = np.diff(z) / (np.abs(np.linspace(-optim_region, +optim_region, len(z) - 1)) + 1) ** (1 / 2)

        #            f = plt.figure()
        #            plt.subplot(121)
        #            plt.plot(z)
        #            plt.subplot(122)
        #            plt.plot(diffz)

        # find sharpest bright-to-dark transition. Maybe check if there are similar transitions in the line and only take the closest one
        minDeriv = np.argmax(np.abs(diffz)) + 1
        print(minDeriv)
        return (xpoints[minDeriv], ypoints[minDeriv])

    # optimizes a knot along an (approximate) normal to the curve, going from inside the ROI to outside
    def optimizeKnot3(self, roi, knotIndex):

        knot = roi.getKnot(knotIndex)
        nextKnot = roi.getKnot(knotIndex + 1)
        prevKnot = roi.getKnot(knotIndex - 1)

        # print "optimizeKnot3"

        optim_region = 5
        optim_region_points = optim_region * 4  # subpixel resolution

        # special case vertical line
        if prevKnot[0] == nextKnot[0]:
            # optimize along a horizontal line
            ypoints = knot[1] * np.ones((2 * optim_region_points))

            # define inside/outside
            if knot[0] < prevKnot[0]:
                xpoints = np.linspace(knot[0] + optim_region, knot[0] - optim_region, 2 * optim_region_points)
            else:
                xpoints = np.linspace(knot[0] - optim_region, knot[0] + optim_region, 2 * optim_region_points)
            z = ndimage.map_coordinates(self.image, np.vstack((ypoints, xpoints))).astype(np.float32)
        elif prevKnot[1] == nextKnot[1]:  # special case horizontal line
            # optimize along a horizontal line
            xpoints = knot[0] * np.ones((2 * optim_region_points))
            if knot[1] < prevKnot[1]:
                ypoints = np.linspace(knot[1] + optim_region, knot[1] - optim_region, 2 * optim_region_points)
            else:
                ypoints = np.linspace(knot[1] - optim_region, knot[1] + optim_region, 2 * optim_region_points)
            z = ndimage.map_coordinates(self.image, np.vstack((ypoints, xpoints))).astype(np.float32)
        else:
            slope = (nextKnot[1] - prevKnot[1]) / (nextKnot[0] - prevKnot[0])
            slope_perpendicular = -1 / slope
            x_dist = np.sqrt(optim_region / (
                    slope_perpendicular ** 2 + 1))  # solving the system (y1-y0) = m(x1-x0) and (y1-y0)^2 + (x1-x0)^2 = d

            # this point is just on the right of our knot.
            test_point_x = knot[0] + 1
            test_point_y = knot[1] + slope_perpendicular * 1

            # if the point is inside the ROI, then calculate the line from right to left
            if roi.isPointInside((test_point_x, test_point_y)):
                x_dist = -x_dist

            # define inside*outside perimeter. Check line intersection. Is this happening on the right or on the left of the point? Right: go from high x to low x
            # x_intersection = (slope_perpendicular*knot[0] - knot[1] - slope*prevKnot[0] + prevKnot[1])/(slope_perpendicular-slope)
            # print knot[0]
            # print x_intersection
            # if x_intersection > knot[0]: x_dist = -x_dist

            x_min = knot[0] - x_dist
            x_max = knot[0] + x_dist
            y_min = knot[1] - slope_perpendicular * x_dist
            y_max = knot[1] + slope_perpendicular * x_dist
            xpoints = np.linspace(x_min, x_max, 2 * optim_region_points)
            ypoints = np.linspace(y_min, y_max, 2 * optim_region_points)
            z = ndimage.map_coordinates(self.image, np.vstack((ypoints, xpoints))).astype(np.float32)

        # sensitive to bright-to-dark
        # diffz = np.diff(z) / (np.abs(np.linspace(-optim_region,+optim_region,len(z)-1))+1)**(1/2)

        # sensitive to all edges
        diffz = -np.abs(np.diff(z)) / (np.abs(np.linspace(-optim_region, +optim_region, len(z) - 1)) + 1) ** (1 / 2)

        #        f = plt.figure()
        #        plt.subplot(121)
        #        plt.plot(z)
        #        plt.subplot(122)
        #        plt.plot(diffz)

        # find sharpest bright-to-dark transition. Maybe check if there are similar transitions in the line and only take the closest one
        minDeriv = np.argmin(diffz)
        # print minDeriv
        return (xpoints[minDeriv], ypoints[minDeriv])

    # optimizes a knot along a radius from the center of the ROI
    def optimizeKnot(self, center, knot):

        optim_region = 5  # voxels

        distanceX = knot[0] - center[0]
        distanceY = knot[1] - center[1]
        npoints = int(np.max([abs(2 * distanceX), abs(2 * distanceY)]))
        xpoints = center[0] + np.linspace(0, 2 * distanceX, npoints)
        ypoints = center[1] + np.linspace(0, 2 * distanceY, npoints)

        # restrict to region aroung the knot
        minIndex = np.max([0, int(npoints / 2 - optim_region)])
        maxIndex = np.min([int(npoints / 2 + optim_region), npoints])

        xpoints = xpoints[minIndex:maxIndex]
        ypoints = ypoints[minIndex:maxIndex]

        # print xpoints
        # print ypoints
        z = ndimage.map_coordinates(self.image, np.vstack((ypoints, xpoints))).astype(np.float32)
        diffz = np.diff(z) / (np.abs(np.array(range(len(z) - 1)) - (len(z) - 1) / 2) ** 2 + 1)

        #        f = plt.figure()
        #        plt.subplot(121)
        #        plt.plot(z)
        #        plt.subplot(122)
        #        plt.plot(diffz)

        # find sharpest bright-to-dark transition. Maybe check if there are similar transitions in the line and only take the closest one
        minDeriv = np.argmin(diffz) + 1
        return (xpoints[minDeriv], ypoints[minDeriv])

    def runTransformix(self, knots, transform):
        transformixImageFilter = sitk.TransformixImageFilter()

        transformixImageFilter.SetLogToConsole(False)
        transformixImageFilter.SetLogToFile(False)

        transformixImageFilter.SetTransformParameterMap(transform)

        # create Transformix point file
        with open("TransformixPoints.txt", "w") as f:
            f.write("point\n")
            f.write("%d\n" % (len(knots)))
            for k in knots:
                # f.write("%.3f %.3f\n" % (k[0], k[1]))
                f.write("%.3f %.3f\n" % (k[0], k[1]))

        transformixImageFilter.SetFixedPointSetFileName("TransformixPoints.txt")
        transformixImageFilter.SetOutputDirectory(".")
        transformixImageFilter.Execute()

        outputCoordRE = re.compile("OutputPoint\s*=\s*\[\s*([\d.]+)\s+([\d.]+)\s*\]")

        knotsOut = []

        with open("outputpoints.txt", "r") as f:
            for line in f:
                m = outputCoordRE.search(line)
                knot = (float(m.group(1)), float(m.group(2)))
                knotsOut.append(knot)

        return knotsOut

    @snapshotSaver
    def propagate(self):
        if self.curImage >= len(self.imList) - 1: return
        # fixedImage = self.image
        # movingImage = self.imList[int(self.curImage+1)]
        curROI = self.getCurrentROI()
        nextROI = self.getCurrentROI(+1)

        qbar = QProgressBar()
        qbar.setRange(0, 3)
        qbar.setWindowTitle(QString("Propagating"))
        qbar.setWindowModality(Qt.ApplicationModal)
        qbar.move(800, 500)
        qbar.show()

        qbar.setValue(0)
        plt.pause(.000001)

        knotsOut = self.runTransformix(curROI.knots, self.getTransform(int(self.curImage)))

        if len(nextROI.knots) < 3:
            nextROI.removeAllKnots()
            nextROI.addKnots(knotsOut)
        else:
            print("Optimizing existing knots")
            for k in knotsOut:
                i = nextROI.findNearestKnot(k)
                oldK = nextROI.getKnot(i)
                newK = ((oldK[0] + k[0]) / 2, (oldK[1] + k[1]) / 2)
                # print "oldK", oldK, "new", k, "mid", newK
                nextROI.replaceKnot(i, newK)

        self.curImage += 1
        self.displayImage(self.imList[int(self.curImage)], self.cmap)
        self.redraw()

        qbar.setValue(1)
        plt.pause(.000001)

        self.simplify()

        qbar.setValue(2)
        plt.pause(.000001)
        self.optimize()
        qbar.close()

    @snapshotSaver
    def propagateBack(self):
        if self.curImage < 1: return
        # fixedImage = self.image
        # movingImage = self.imList[int(self.curImage+1)]
        curROI = self.getCurrentROI()
        nextROI = self.getCurrentROI(-1)

        qbar = QProgressBar()
        qbar.setRange(0, 3)
        qbar.setWindowTitle(QString("Propagating"))
        qbar.setWindowModality(Qt.ApplicationModal)
        qbar.move(800, 500)
        qbar.show()

        qbar.setValue(0)
        plt.pause(.000001)

        knotsOut = self.runTransformix(curROI.knots, self.getInverseTransform(int(self.curImage)))

        if len(nextROI.knots) < 3:
            nextROI.removeAllKnots()
            nextROI.addKnots(knotsOut)
        else:
            print("Optimizing existing knots")
            for k in knotsOut:
                i = nextROI.findNearestKnot(k)
                oldK = nextROI.getKnot(i)
                newK = ((oldK[0] + k[0]) / 2, (oldK[1] + k[1]) / 2)
                nextROI.replaceKnot(i, newK)

        qbar.setValue(1)
        plt.pause(.000001)

        self.curImage -= 1
        self.displayImage(self.imList[int(self.curImage)], self.cmap)
        self.redraw()

        qbar.setValue(2)
        plt.pause(.000001)

        self.simplify()

        qbar.setValue(3)
        plt.pause(.000001)
        self.optimize()

        qbar.close()

    # No @snapshotSaver: snapshot is saved in the calling function
    def addPoint(self, spline, event):
        self.currentPoint = spline.addKnot((event.xdata, event.ydata))
        # self.redraw()
        self.redraw()

    # No @snapshotSaver: snapshot is saved in the calling function
    def movePoint(self, spline, event):
        if self.currentPoint is None:
            return

        spline.replaceKnot(self.currentPoint, (event.xdata, event.ydata))
        # self.redraw()
        self.redraw()


    @pyqtSlot()
    @snapshotSaver
    def clearCurrentROI(self):
        if self.editMode == ToolboxWindow.EDITMODE_CONTOUR:
            roi = self.getCurrentROI()
            roi.removeAllKnots()
        elif self.editMode == ToolboxWindow.EDITMODE_MASK:
            self.roiManager.clear_mask(self.getCurrentROIName(), self.curImage)
            self.activeMask = None
        self.redraw()


    def getCurrentROIName(self):
        return self.toolbox_window.get_current_roi_subroi()[0]

    def getCurrentSubroiNumber(self):
        return self.toolbox_window.get_current_roi_subroi()[1]

    def _getSetCurrentROI(self, offset=0, newROI=None):
        # TODO: Move to ROIManager
        if not self.getCurrentROIName():
            return None

        imageN = int(self.curImage + offset)
        curName = self.getCurrentROIName()
        curSubroi = self.getCurrentSubroiNumber()

        #print("Get set ROI", curName, imageN, curSubroi)

        return self.roiManager._get_set_roi(curName, imageN, curSubroi, newROI)

    def getCurrentROI(self, offset=0):
        return self._getSetCurrentROI(offset)

    def setCurrentROI(self, r, offset=0):
        self._getSetCurrentROI(offset, r)


    ##############################################################################################################
    ###
    ### Displaying
    ###
    ###############################################################################################################


    def removeMasks(self):
        """ Remove the masks from the plot """
        print('Removing masks')
        try:
            self.maskImPlot.remove()
        except:
            pass
        self.maskImPlot = None

        try:
            self.maskOtherImPlot.remove()
        except:
            pass
        self.maskOtherImPlot = None

        try:
            self.brush_patch.remove()
        except:
            pass
        self.brush_patch = None

    def removeContours(self):
        """ Remove all the contours from the plot """
        self.roiManager.clear(only_clear_interface=True)

    def updateMasksFromROIs(self):
        roi_name = self.getCurrentROIName()
        mask_size = self.image.shape
        self.otherMask = np.zeros(mask_size, dtype=np.uint8)
        self.activeMask = np.zeros(mask_size, dtype=np.uint8)
        for key_tuple, mask in self.roiManager.all_masks(image_number=self.curImage):
            mask_name = key_tuple[0]
            if mask_name == roi_name:
                self.activeMask = mask.copy()
            else:
                self.otherMask = np.logical_or(self.otherMask, mask)

    def drawMasks(self):
        """ Plot the masks for the current figure """
        if self.activeMask is None:
            self.updateMasksFromROIs()

        if not self.hideRois:  # if we hide the ROIs, clear all the masks
            active_mask = self.activeMask
            other_mask = self.otherMask
        else:
            active_mask = np.zeros_like(self.activeMask)
            other_mask = np.zeros_like(self.otherMask)

        if self.maskImPlot is None:
            self.maskImPlot = self.axes.imshow(active_mask, cmap=MASK_LAYER_COLORMAP, alpha=MASK_LAYER_ALPHA, vmin=0, vmax=1, zorder=100)

        self.maskImPlot.set_data(active_mask)

        if self.maskOtherImPlot is None:
            self.maskOtherImPlot = self.axes.imshow(other_mask, cmap=MASK_LAYER_OTHER_COLORMAP, alpha=MASK_LAYER_ALPHA, vmin=0, vmax=1, zorder=101)

        self.maskOtherImPlot.set_data(other_mask)


    def drawContours(self):
        """ Plot the contours for the current figure """
        for key_tuple, roi in self.roiManager.all_rois():
            name, sliceN, subroiNumber = key_tuple
            if sliceN != int(self.curImage) or self.hideRois:
                roi.remove()
            else:
                rSize = 0.1
                rColor = self.roiOther
                if name == self.getCurrentROIName():
                    if subroiNumber == self.getCurrentSubroiNumber():
                        rSize = ROI_CIRCLE_SIZE
                        rColor = self.roiColor
                    else:
                        rColor = self.roiSame
                try:
                    roi.draw(self.axes, rSize, rColor)
                except:
                    pass

    # convert a single slice to ROIs
    def maskToRois2D(self, name, mask, imIndex, refresh = True):
        if not self.roiManager: return
        self.roiManager.set_mask(name, imIndex, mask)
        if refresh:
            self.updateRoiList()
            self.redraw()

    # convert a 2D mask or a 3D dataset to rois
    def masksToRois(self, maskDict, imIndex):
        for name, mask in maskDict.items():
            if len(mask.shape) > 2: # multislice
                for sl in range(mask.shape[3]):
                    self.maskToRois2D(name, mask[:,:,sl], sl, False)
            else:
                self.maskToRois2D(name, mask, imIndex, False)
        self.updateRoiList()
        self.redraw()

    def displayImage(self, im, cmap=None):
        try:
            self.maskImPlot.remove()
        except:
            pass
        try:
            self.maskOtherImPlot.remove()
        except:
            pass
        self.maskImPlot = None
        self.maskOtherImPlot = None
        ImageShow.displayImage(self, im, cmap)
        self.updateRoiList()  # set the appropriate (sub)roi list for the current image
        self.activeMask = None
        self.otherMask = None
        self.toolbox_window.set_class(self.classifications[int(self.curImage)])  # update the classification combo

    ##############################################################################################################
    ###
    ### UI Callbacks
    ###
    ##############################################################################################################

    @pyqtSlot()
    def refreshCB(self):
        # check if ROIs should be autosaved
        now = datetime.now()
        if (now - self.lastsave).total_seconds() > AUTOSAVE_INTERVAL:
            self.lastsave = now
            self.saveROIPickle()

        if not self.app:
            app = QApplication.instance()

        if self.wacom:
            app.setOverrideCursor(Qt.BlankCursor)
        else:
            app.setOverrideCursor(Qt.ArrowCursor)

        #print("Refresh")
        #print(self.editMode)

        if self.roiManager:
            if self.editMode == ToolboxWindow.EDITMODE_CONTOUR:
                self.drawContours()
            elif self.editMode == ToolboxWindow.EDITMODE_MASK:
                self.drawMasks()

        #print("Redrawing")
        #print(self.axes.get_children())
        #plt.draw() - already in redraw

    def closeCB(self, event):
        if not self.basepath: return
        self.toolbox_window.close()
        if self.transformsChanged: self.pickleTransforms()
        self.saveROIPickle()

    def moveBrushPatch(self, event):
        """
            moves the brush. Returns True if the brush was moved to a new position
        """
        brush_type, brush_size = self.toolbox_window.get_brush()
        mouseX = event.xdata
        mouseY = event.ydata
        if self.toolbox_window.get_edit_button_state() == ToolboxWindow.ADD_STATE:
            brush_color = BRUSH_PAINT_COLOR
        elif self.toolbox_window.get_edit_button_state() == ToolboxWindow.REMOVE_STATE:
            brush_color = BRUSH_ERASE_COLOR
        else:
            brush_color = None
        if mouseX is None or mouseY is None or brush_color is None:
            try:
                self.brush_patch.remove()
                self.fig.canvas.draw()
            except:
                pass
            self.brush_patch = None
            return False

        try:
            oldX = self.moveBrushPatch_oldX  # static variables
            oldY = self.moveBrushPatch_oldY
        except:
            oldX = -1
            oldY = -1

        mouseX = np.round(mouseX)
        mouseY = np.round(mouseY)

        if oldX == mouseX and oldY == mouseY:
            return False

        self.moveBrushPatch_oldX = mouseX
        self.moveBrushPatch_oldY = mouseY

        if brush_type == ToolboxWindow.BRUSH_SQUARE:
            xy = (math.floor(mouseX - brush_size / 2) + 0.5, math.floor(mouseY - brush_size / 2) + 0.5)
            if type(self.brush_patch) != SquareBrush:
                try:
                    self.brush_patch.remove()
                except:
                    pass
                self.brush_patch = SquareBrush(xy, brush_size, brush_size, color=brush_color)
                self.axes.add_patch(self.brush_patch)

            self.brush_patch.set_xy(xy)
            self.brush_patch.set_height(brush_size)
            self.brush_patch.set_width(brush_size)

        elif brush_type == ToolboxWindow.BRUSH_CIRCLE:
            center = (math.floor(mouseX) + 0.5, math.floor(mouseY) + 0.5)
            if type(self.brush_patch) != PixelatedCircleBrush:
                try:
                    self.brush_patch.remove()
                except:
                    pass
                self.brush_patch = PixelatedCircleBrush(center, brush_size, color=brush_color)
                self.axes.add_patch(self.brush_patch)

            self.brush_patch.set_center(center)
            self.brush_patch.set_radius(brush_size)

        self.brush_patch.set_color(brush_color)
        self.fig.canvas.draw()
        return True

    def modifyMaskFromBrush(self, saveSnapshot=False):
        if not self.brush_patch: return
        if self.toolbox_window.get_edit_button_state() == ToolboxWindow.ADD_STATE:
            if saveSnapshot: self.saveSnapshot()
            np.logical_or(self.activeMask, self.brush_patch.to_mask(self.activeMask.shape), out=self.activeMask)
        elif self.toolbox_window.get_edit_button_state() == ToolboxWindow.REMOVE_STATE:
            if saveSnapshot: self.saveSnapshot()
            np.logical_and(self.activeMask, np.logical_not(self.brush_patch.to_mask(self.activeMask.shape)),
                           out=self.activeMask)
        self.redraw()

    # override from ImageShow
    def mouseMoveCB(self, event):
        if (self.getState() == 'MUSCLE' and
                self.toolbox_window.get_edit_mode() == ToolboxWindow.EDITMODE_MASK and
                self.isCursorNormal() and
                event.button != 2 and
                event.button != 3):
            moved_to_new_point = self.moveBrushPatch(event)
            if event.button == 1: # because we are overriding MoveCB, we won't call leftPressCB
                if moved_to_new_point:
                    self.modifyMaskFromBrush()
        else:
            if self.brush_patch:
                try:
                    self.brush_patch.remove()
                except:
                    pass
                self.brush_patch = None
            ImageShow.mouseMoveCB(self, event)

    def leftMoveCB(self, event):
        if self.getState() == 'MUSCLE':
            roi = self.getCurrentROI()
            if self.toolbox_window.get_edit_button_state() == ToolboxWindow.ADD_STATE:  # event.key == 'shift' or checkCapsLock():
                self.movePoint(roi, event)

    def leftPressCB(self, event):
        print("left press", self.getState())
        if not self.imPlot.contains(event):
            print("Event outside")
            return

        if self.getState() != 'MUSCLE': return

        if self.toolbox_window.get_edit_mode() == ToolboxWindow.EDITMODE_MASK:
            self.modifyMaskFromBrush(saveSnapshot=True)
        else:
            roi = self.getCurrentROI()
            knotIndex, knot = roi.findKnotEvent(event)
            print("Left press", roi, knot)
            if self.toolbox_window.get_edit_button_state() == ToolboxWindow.REMOVE_STATE:
                if knotIndex is not None:
                    self.saveSnapshot()
                    roi.removeKnot(knotIndex)
                    # self.redraw()
                    self.redraw()
            elif self.toolbox_window.get_edit_button_state() == ToolboxWindow.ADD_STATE:
                self.saveSnapshot()
                if knotIndex is None:
                    self.addPoint(roi, event)
                else:
                    self.currentPoint = knotIndex

    def leftReleaseCB(self, event):
        self.currentPoint = None  # reset the state
        if self.editMode == ToolboxWindow.EDITMODE_MASK:
            self.roiManager.set_mask(self.getCurrentROIName(), self.curImage, self.activeMask)

    def rightPressCB(self, event):
        self.hideRois = HIDE_ROIS_RIGHTCLICK
        self.redraw()

    def rightReleaseCB(self, event):
        self.hideRois = False
        self.redraw()

    def keyPressCB(self, event):
        # print(event.key)
        if 'shift' in event.key:
            self.toolbox_window.set_temp_edit_button_state(ToolboxWindow.ADD_STATE)
        elif 'control' in event.key or 'cmd' in event.key or 'super' in event.key or 'ctrl' in event.key:
            self.toolbox_window.set_temp_edit_button_state(ToolboxWindow.REMOVE_STATE)
        if event.key == 'n':
            self.propagate()
        elif event.key == 'b':
            self.propagateBack()
        else:
            ImageShow.keyPressCB(self, event)

    def keyReleaseCB(self, event):
        if 'shift' in event.key or 'control' in event.key or 'cmd' in event.key or 'super' in event.key or 'ctrl' in event.key:
            self.toolbox_window.restore_edit_button_state()

        # plt.show()

    ################################################################################################################
    ###
    ### I/O
    ###
    ################################################################################################################

    @pyqtSlot(str)
    def saveROIPickle(self, roiPickleName=None):
        if not roiPickleName:
            roiPickleName = self.getRoiFileName()
        print("Saving ROIs", roiPickleName)
        if self.roiManager and not self.roiManager.is_empty():  # make sure ROIs are not empty
            pickle.dump(self.roiManager, open(roiPickleName, 'wb'))

    @pyqtSlot(str)
    def loadROIPickle(self, roiPickleName=None):
        if not roiPickleName:
            roiPickleName = self.getRoiFileName()
        print("Loading ROIs", roiPickleName)
        try:
            roiManager = pickle.load(open(roiPickleName, 'rb'))
        except UnicodeDecodeError:
            print('Warning: Unicode decode error')
            roiManager = pickle.load(open(roiPickleName, 'rb'), encoding='latin1')
        except:
            print("Unspecified error")
            return

        try:

            # print(self.allROIs)
            assert type(roiManager) == ROIManager
        except:
            print("Unrecognized saved ROI type")
            return

        print('Rois loaded')
        self.clearAllROIs()
        self.roiManager = roiManager
        self.updateRoiList()

    @pyqtSlot(str)
    def loadDirectory(self, path):
        self.imList = []
        self.originalSegmentationMasks = {}
        ImageShow.loadDirectory(self, path)
        roi_bak_name = self.getRoiFileName() + '.' + datetime.now().strftime('%Y%m%d%H%M%S')
        try:
            shutil.copyfile(self.getRoiFileName(), roi_bak_name)
        except:
            print("Warning: cannot copy roi file")
        self.roiManager = ROIManager(self.imList[0].shape)
        self.unPickleTransforms()
        #self.loadROIPickle()
        self.redraw()
        self.toolbox_window.set_exports_enabled(numpy= True,
                                                dicom= (self.dicomHeaderList is not None),
                                                nifti= (self.affine is not None)
                                                )

    def appendImage(self, im):
        ImageShow.appendImage(self, im)
        print("new Append Image")
        if not self.dl_classifier: return
        class_input = {'image': self.imList[-1], 'resolution': self.resolution[0:2]}
        #class_str = self.dl_classifier(class_input)
        class_str = 'Thigh' # DEBUG
        print("Classification", class_str)
        self.classifications.append(class_str)

    @pyqtSlot(str, str)
    def saveResults(self, pathOut: str, outputType: str):
        # outputType is 'dicom', 'npy', 'npz', 'nifti'
        print("Saving results...")
        imSize = self.image.shape

        allMasks = {}
        diceScores = []

        dataForTraining = {}
        segForTraining = {}

        for roiName in self.roiManager.get_roi_names():
            masklist = []
            for imageIndex in range(len(self.imList)):
                roi = np.zeros(imSize)
                if self.roiManager.contains(roiName, imageIndex):
                    roi = self.roiManager.get_mask(roiName, imageIndex)
                masklist.append(roi)
                try:
                    originalSegmentation = self.originalSegmentationMasks[imageIndex][roiName]
                except:
                    originalSegmentation = None

                if originalSegmentation is not None:
                    diceScores.append(calc_dice_score(originalSegmentation, roi))
                    print(diceScores)

                # TODO: maybe add this to the training according to the dice score?
                classification_name = self.classifications[imageIndex]
                if classification_name not in dataForTraining:
                    dataForTraining[classification_name] = {}
                    segForTraining[classification_name] = {}
                if imageIndex not in dataForTraining[classification_name]:
                    dataForTraining[classification_name][imageIndex] = self.imList[imageIndex]
                    segForTraining[classification_name][imageIndex] = {}

                segForTraining[classification_name][imageIndex][roiName] = roi

            print("Saving %s..." % (roiName))
            npMask = np.transpose(np.stack(masklist), [1, 2, 0])
            allMasks[roiName] = npMask

        diceScores = np.array(diceScores)
        print(diceScores)
        print("Average Dice score", np.array(diceScores).mean())

        # perform incremental learning
        for classification_name in dataForTraining:
            print(f'Performing incremental learning for {classification_name}')
            try:
                model = self.dl_segmenters[classification_name]
            except KeyError:
                model = self.model_provider.load_model(classification_name)
                self.dl_segmenters[classification_name] = model
            training_data = []
            training_outputs = []
            for imageIndex in dataForTraining[classification_name]:
                training_data.append(dataForTraining[classification_name][imageIndex])
                training_outputs.append(segForTraining[classification_name][imageIndex])
            model.incremental_learn({'image_list': training_data, 'resolution': self.resolution[0:2]}, training_outputs)
            print('Done')

        #TODO: send the model back to the server

        if outputType == 'dicom':
            save_dicom_masks(pathOut, allMasks, self.dicomHeaderList)
        elif outputType == 'nifti':
            save_nifti_masks(pathOut, allMasks, self.affine, self.transpose)
        elif outputType == 'npy':
            save_npy_masks(pathOut, allMasks)
        else: # assume the most generic outputType == 'npz':
            save_npz_masks(pathOut, allMasks)

    def pickleTransforms(self):
        if not self.basepath: return
        pickleObj = {}
        transformDict = {}
        for k, transformList in self.transforms.items():
            curTransformList = []
            for transform in transformList:
                curTransformList.append(transform.asdict())
            transformDict[k] = curTransformList
        invTransformDict = {}
        for k, transformList in self.invtransforms.items():
            curTransformList = []
            for transform in transformList:
                curTransformList.append(transform.asdict())
            invTransformDict[k] = curTransformList
        pickleObj['direct'] = transformDict
        pickleObj['inverse'] = invTransformDict
        outFile = os.path.join(self.basepath, 'transforms.p')
        pickle.dump(pickleObj, open(outFile, 'wb'))

    def unPickleTransforms(self):
        if not self.basepath: return
        pickleFile = os.path.join(self.basepath, 'transforms.p')
        try:
            pickleObj = pickle.load(open(pickleFile, 'rb'))
        except:
            print("Error trying to load transforms")
            return

        transformDict = pickleObj['direct']
        self.transforms = {}
        for k, transformList in transformDict.items():
            curTransformList = []
            for transform in transformList:
                curTransformList.append(sitk.ParameterMap(transform))
            self.transforms[k] = tuple(curTransformList)
        invTransformDict = pickleObj['inverse']
        self.invtransforms = {}
        for k, transformList in invTransformDict.items():
            curTransformList = []
            for transform in transformList:
                curTransformList.append(sitk.ParameterMap(transform))
            self.invtransforms[k] = tuple(curTransformList)

    ########################################################################################
    ###
    ### Deep learning functions
    ###
    ########################################################################################

    def setModelProvider(self, modelProvider):
        self.model_provider = modelProvider
        self.dl_classifier = modelProvider.load_model('Classifier')

    def setAvailableClasses(self, classList):
        self.toolbox_window.set_available_classes(classList)

    @pyqtSlot(str)
    def changeClassification(self, newClass):
        self.classifications[int(self.curImage)] = newClass

    @pyqtSlot()
    @snapshotSaver
    def doSegmentation(self):
        # run the segmentation
        imIndex = int(self.curImage)
        class_str = self.classifications[imIndex]
        try:
            segmenter = self.dl_segmenters[class_str]
        except KeyError:
            segmenter = self.model_provider.load_model(class_str)
            self.dl_segmenters[class_str] = segmenter

        t = time.time()
        inputData = {'image': self.imList[imIndex], 'resolution': self.resolution[0:2]}
        print("Segmenting image...")
        masks_out = segmenter(inputData)
        self.originalSegmentationMasks[imIndex] = masks_out # save original segmentation for statistics
        print("Done")
        self.masksToRois(masks_out, imIndex)
        self.activeMask = None
        self.otherMask = None
        print("Segmentation/import time:", time.time() - t)
        self.redraw()
