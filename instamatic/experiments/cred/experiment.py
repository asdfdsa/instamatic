import os
import datetime
import logging
from Tkinter import *
import numpy as np
import glob
import time
import ImgConversion
from instamatic import config
from instamatic.formats import write_tiff
from skimage.feature import register_translation
from instamatic.calibrate import CalibBeamShift
from instamatic.calibrate.calibrate_beamshift import calibrate_beamshift
from scipy import ndimage
from instamatic.processing.find_holes import find_holes

# degrees to rotate before activating data collection procedure
ACTIVATION_THRESHOLD = 0.2

def Calibrate_Imageshift(ctrl):
    from instamatic.calibrate.fit import fit_affine_transformation
    from instamatic.processing.cross_correlate import cross_correlate

    x0, y0 = ctrl.imageshift.get()
    img_cent, h_cent = ctrl.getImage(exposure=0.1, comment="Beam in center of image")

    shifts = []
    imgpos = []

    inp = raw_input("""Calibrate imageshift
-------------------
 1. Go to diffraction mode with CL 25 cm.
 2. Focus the diffraction spots.
 3. Center the beam with PLA.
    
 >> Press <ENTER> to start >> \n""")

    for i in range(0,5):
        for j in range(0,5):
            ctrl.imageshift.set(x= x0 + (i-2)*2500, y= y0 + (j-2)*2500)
            img, h = ctrl.getImage(exposure = 0.1, comment = "imageshifted image")

            shift = cross_correlate(img_cent, img, upsample_factor=10, verbose=False)
            imgshift = np.array((x0 + (i-2)*2500, y0 + (j-2)*2500))
            imgpos.append(imgshift)
            shifts.append(shift)

    ctrl.imageshift.set(x = x0, y = y0)

    r, t = fit_affine_transformation(shifts, imgpos)

    print "ImageShift calibration done."

    return r

"""fast_finder is contribution from Dr Jonas Angstrom"""
def fast_finder(image, treshold=1):
    X = np.mean(image, axis=0)
    Y = np.mean(image, axis=1)
    im_mean = np.mean(X)
    rads = np.zeros(2)
    center = np.zeros(2)
    for n, XY in enumerate([X,Y]):
        over = np.where(XY>(im_mean*treshold))[0]
        rads[n] = (over[-1] - over[0])/2
        center[n] = over[0] + rads[n]
    return center, rads

class Experiment(object):
    def __init__(self, ctrl, expt, stopEvent, unblank_beam=False, path=None, log=None, flatfield=None):
        super(Experiment,self).__init__()
        self.ctrl = ctrl
        self.path = path
        self.expt = expt
        self.unblank_beam = unblank_beam
        self.logger = log
        self.camtype = ctrl.cam.name
        self.stopEvent = stopEvent
        self.flatfield = flatfield

        self.diff_defocus = 0
        self.image_interval = 99999
        
        self.mode = "initial"

    def report_status(self):
        self.image_binsize = self.ctrl.cam.default_binsize
        self.magnification = self.ctrl.magnification.value
        self.image_spotsize = self.ctrl.spotsize
        
        self.diff_binsize = self.image_binsize
        self.diff_exposure = self.expt
        self.diff_brightness = self.ctrl.brightness.value
        self.diff_spotsize = self.image_spotsize
        print "Output directory:\n{}".format(self.path)
        print "Imaging     : binsize = {}".format(self.image_binsize)
        print "              exposure = {}".format(self.expt)
        print "              magnification = {}".format(self.magnification)
        print "              spotsize = {}".format(self.image_spotsize)
        print "Diffraction : binsize = {}".format(self.diff_binsize)
        print "              exposure = {}".format(self.diff_exposure)
        print "              brightness = {}".format(self.diff_brightness)
        print "              spotsize = {}".format(self.diff_spotsize)        
    
    def enable_image_interval(self, interval, defocus):
        self.diff_defocus = defocus
        self.image_interval = interval
        print "Image interval enabled: every {} frames an image with defocus value {} will be displayed.".format(interval, defocus)
        self.mode = "manual"
        
    def enable_autotrack(self, interval, defocus):
        self.diff_defocus = defocus
        self.image_interval = interval
        print "Image autotrack enabled: every {} frames an image with defocus value {} will be displayed.".format(interval, defocus)
        self.mode = "auto"
        
    def start_collection(self):
        a = a0 = self.ctrl.stageposition.a
        spotsize = self.ctrl.spotsize
        
        self.pathtiff = os.path.join(self.path,"tiff")
        self.pathsmv = os.path.join(self.path,"SMV")
        self.pathred = os.path.join(self.path,"RED")
        
        for path in (self.path, self.pathtiff, self.pathsmv, self.pathred):
            if not os.path.exists(path):
                os.makedirs(path)
        
        self.logger.info("Data recording started at: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self.logger.info("Data saving path: {}".format(self.path))
        self.logger.info("Data collection exposure time: {} s".format(self.expt))
        self.logger.info("Data collection spot size: {}".format(spotsize))
        
        # TODO: Mostly above is setup, split off into own function

        buffer = []
        image_buffer = []    
            
        if self.mode == "auto":
            print "Auto tracking-back mode activated. Please ensure that you put the particle in the middle of the aperture."

            transform_imgshift = Calibrate_Imageshift(self.ctrl)
            ## find the center of the particle and circle a 50*50 area for reference for correlate2d
            try:
                self.calib_beamshift = CalibBeamShift.from_file()
            except IOError:
                print "No calibration result found. Running instamatic.calibrate_beamshift first...\n"
                calib_file = "x"
                while calib_file == "x":
                    ## Here maybe better to calibrate beam shift in diffraction defocus mode, choose defocus value of the defocus you wish to use.
                    print "Find a clear area, toggle the beam to the desired defocus value."
                    calibrate_beamshift(ctrl = self.ctrl)
                    print "Beam shift calibration done."
                    calib_file = raw_input("Find your particle, go back to diffraction mode, and press ENTER when ready to continue. Press x to REDO the calibration.")
            
            diff_focus_proper = self.ctrl.difffocus.value
            diff_focus_defocused = self.diff_defocus 
            self.ctrl.difffocus.value = diff_focus_defocused
            img0, h = self.ctrl.getImage(self.expt /5.0, header_keys=None)
            self.ctrl.difffocus.value = diff_focus_proper

            bs_x0, bs_y0 = self.ctrl.beamshift.get()

            print bs_x0, bs_y0
            ## need to find accurately the center of the particle, which is quite difficult with the defocused images with SA aperture.
            ## remember to using focused beam instead of using SA aperture
            """img_sobel = ndimage.filters.sobel(img0)
            bn_img = np.zeros(img_sobel.shape[0],img_sobel.shape[1])
            sbl_max = np.amax(abs(img_sobel))
            bn_img = np.abs(img_sobel) >= (sbl_max/10.0)
            crystal_pos = np.average(np.where(bn_img == True),axis = 1)"""
            aperture_position = find_holes(img0.astype(float), area = 500, plot = False)

            if len(aperture_position) == 1:
                for i, prop in enumerate(aperture_position):
                    cy, cx = prop.weighted_centroid
                    y1,x1,y2,x2 = [x for x in prop.bbox]
                
                crystal_pos = [cy, cx]
                d_SA = y2-y1
                window_size = int(d_SA/1.414)
                if window_size % 2 == 1:
                    window_size = window_size + 1
                
                a1 = int(crystal_pos[0]-window_size/2)
                b1 = int(crystal_pos[0]+window_size/2)
                a2 = int(crystal_pos[1]-window_size/2)
                b2 = int(crystal_pos[1]+window_size/2)
                img0_cropped = img0[a1:b1,a2:b2]
                self.logger.debug("Cropped image size: {}".format(img0_cropped.shape))
                cc = (0,0)
                
            else:
                print "Please find another nicely SEPARATED crystal for autotracking."
                return None

        ## Instead of beam shift, try image shift. Calibrate imageshift first.
        #if self.mode == "auto":
        #    print "Auto tracking-back mode activated. Please ensure that you put the particle in the middle of the aperture"
            
            
        if self.camtype == "simulate":
            self.startangle = a
        else:
            while abs(a - a0) < ACTIVATION_THRESHOLD:
                a = self.ctrl.stageposition.a
                if abs(a - a0) > ACTIVATION_THRESHOLD:
                    break
            print "Data Recording started."
            self.startangle = a

        if self.unblank_beam:
            print "Unblanking beam"
            self.ctrl.beamblank = False

        i = 1

        print self.mode

        self.ctrl.cam.block()

        t0 = time.time()

        while not self.stopEvent.is_set():
            
            if self.mode == "auto":
                if i % self.image_interval == 0:
                    t_start = time.time()
                    acquisition_time = (t_start - t0) / (i-1)
    
                    self.ctrl.difffocus.value = diff_focus_defocused
                    img, h = self.ctrl.getImage(self.expt / 10.0, header_keys=None)
                    self.ctrl.difffocus.value = diff_focus_proper
    
                    image_buffer.append((i, img, h))
                    print "{} saved to image_buffer".format(i)

                    """if i < 15:
                        aperture_position = find_holes(img.astype(float), area = 500, plot = False, verbose = False)

                        if len(aperture_position) == 1:
                            for j, prop in enumerate(aperture_position):
                                cy, cx = prop.weighted_centroid
                                y1,x1,y2,x2 = [x for x in prop.bbox]

                                crystal_pos = [cy, cx]
                                #d_SA = y2-y1
                                #window_size = d_SA/1.414
                                
                                a1 = int(crystal_pos[0]-window_size/2)
                                b1 = int(crystal_pos[0]+window_size/2)
                                a2 = int(crystal_pos[1]-window_size/2)
                                b2 = int(crystal_pos[1]+window_size/2)
                                img_cropped = img[a1:b1,a2:b2]
                                self.logger.debug("crystal_pos: {} by find_hole.".format(crystal_pos))
                        else:
                            print "At least two apertures found!! Exitting auto mode..."
                            break
                    ## Here we can use imageshift suggested by S. Smeets
                    ## Presumably the position of the beam (aperture) does not move; and then we can crop the image at the same position with same cropping size.
                    #img_cropped = img[a1:b1,a2:b2]
                    else:
                        crystal_pos = crystal_pos + cc
                        self.logger.debug("crystal_pos: {} by add cc only.".format(crystal_pos))
                        a1 = int(crystal_pos[0]-window_size/2)
                        b1 = int(crystal_pos[0]+window_size/2)
                        a2 = int(crystal_pos[1]-window_size/2)
                        b2 = int(crystal_pos[1]+window_size/2)
                        img_cropped = img[a1:b1,a2:b2]"""

                    aperture_position = find_holes(img.astype(float), area = 500, plot = False, verbose = False)

                    if len(aperture_position) == 1:
                        for j, prop in enumerate(aperture_position):
                            cy, cx = prop.weighted_centroid
                            y1,x1,y2,x2 = [x for x in prop.bbox]

                            crystal_pos = [cy, cx]
                            #d_SA = y2-y1
                            #window_size = d_SA/1.414
                            
                            a1 = int(crystal_pos[0]-window_size/2)
                            b1 = int(crystal_pos[0]+window_size/2)
                            a2 = int(crystal_pos[1]-window_size/2)
                            b2 = int(crystal_pos[1]+window_size/2)
                            img_cropped = img[a1:b1,a2:b2]
                            self.logger.debug("crystal_pos: {} by find_hole.".format(crystal_pos))
                    else:
                        print "At least two apertures found!! Exitting auto mode..."
                        break

                    """crystal_pos, r = fast_finder(img)

                    self.logger.debug("crystal_pos: {} by fast_finder.".format(crystal_pos))

                    a1 = int(crystal_pos[0]-window_size/2)
                    b1 = int(crystal_pos[0]+window_size/2)
                    a2 = int(crystal_pos[1]-window_size/2)
                    b2 = int(crystal_pos[1]+window_size/2)
                    img_cropped = img[a1:b1,a2:b2]"""

                    cc,err,diffphase = register_translation(img0_cropped,img_cropped)

                    self.logger.debug("Cross correlation result: {}".format(cc))
                    
                    delta_beamshiftcoord = np.matmul(self.calib_beamshift.transform, cc)
                    self.logger.debug("Beam shift coordinates: {}".format(delta_beamshiftcoord))
                    self.ctrl.beamshift.set(bs_x0 + delta_beamshiftcoord[0], bs_y0 + delta_beamshiftcoord[1])
                    bs_x0 = bs_x0 + delta_beamshiftcoord[0]
                    bs_y0 = bs_y0 + delta_beamshiftcoord[1]
                    ## not sure if it should be + or - here
    
                    next_interval = t_start + acquisition_time
                    # print i, "BLOOP! {:.3f} {:.3f} {:.3f}".format(next_interval-t_start, acquisition_time, t_start-t0)
    
                    t = time.time()
    
                    while time.time() > next_interval:
                        next_interval += acquisition_time
                        i += 1
                        # print i, "SKIP!  {:.3f} {:.3f}".format(next_interval-t_start, acquisition_time)
    
                    while time.time() < next_interval:
                        time.sleep(0.001)
    
                else:
                    img, h = self.ctrl.getImage(self.expt, header_keys=None)
                    # print i, "Image!"
                    buffer.append((i, img, h))
                    print "{} saved to buffer".format(i)
    
                i += 1
            
            else:
                if i % self.image_interval == 0:
                    t_start = time.time()
                    acquisition_time = (t_start - t0) / (i-1)
    
                    self.ctrl.difffocus.value = diff_focus_defocused
                    img, h = self.ctrl.getImage(self.expt / 5.0, header_keys=None)
                    self.ctrl.difffocus.value = diff_focus_proper
    
                    image_buffer.append((i, img, h))
    
                    next_interval = t_start + acquisition_time
                    # print i, "BLOOP! {:.3f} {:.3f} {:.3f}".format(next_interval-t_start, acquisition_time, t_start-t0)
    
                    t = time.time()
    
                    while time.time() > next_interval:
                        next_interval += acquisition_time
                        i += 1
                        # print i, "SKIP!  {:.3f} {:.3f}".format(next_interval-t_start, acquisition_time)
    
                    while time.time() < next_interval:
                        time.sleep(0.001)
    
                else:
                    img, h = self.ctrl.getImage(self.expt, header_keys=None)
                    # print i, "Image!"
                    buffer.append((i, img, h))
    
                i += 1

        t1 = time.time()

        self.ctrl.cam.unblock()

        if self.camtype == "simulate":
            self.endangle = self.startangle + np.random.random()*50
            camera_length = 300
        else:
            self.endangle = self.ctrl.stageposition.a
            camera_length = int(self.ctrl.magnification.get())

        if self.unblank_beam:
            print "Blanking beam"
            self.ctrl.beamblank = True

        # TODO: all the rest here is io+logistics, split off in to own function

        print "Rotated {:.2f} degrees from {:.2f} to {:.2f}".format(abs(self.endangle-self.startangle), self.startangle, self.endangle)
        nframes = i + 1 # len(buffer) can lie in case of frame skipping
        osangle = abs(self.endangle - self.startangle) / nframes
        acquisition_time = (t1 - t0) / nframes

        self.logger.info("Data collection camera length: {} mm".format(camera_length))
        self.logger.info("Data collected from {} degree to {} degree.".format(self.startangle, self.endangle))
        self.logger.info("Oscillation angle: {}".format(osangle))
        self.logger.info("Pixel size and actual camera length updated in SMV file headers for DIALS processing.")
        
        with open(os.path.join(self.path, "cRED_log.txt"), "w") as f:
            f.write("Data Collection Time: {}\n".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            f.write("Starting angle: {}\n".format(self.startangle))
            f.write("Ending angle: {}\n".format(self.endangle))
            f.write("Exposure Time: {} s\n".format(self.expt))
            f.write("Spot Size: {}\n".format(spotsize))
            f.write("Camera length: {} mm\n".format(camera_length))
            f.write("Oscillation angle: {} degrees\n".format(osangle))
            f.write("Number of frames: {}\n".format(len(buffer)))

        rotation_angle = config.microscope.camera_rotation_vs_stage_xy

        img_conv = ImgConversion.ImgConversion(buffer=buffer, 
                 camera_length=camera_length,
                 osangle=osangle,
                 startangle=self.startangle,
                 endangle=self.endangle,
                 rotation_angle=rotation_angle,
                 acquisition_time=acquisition_time,
                 resolution_range=(20, 0.8),
                 flatfield=self.flatfield)
        
        img_conv.writeTiff(self.pathtiff)
        img_conv.writeIMG(self.pathsmv)
        img_conv.ED3DCreator(self.pathred)
        img_conv.MRCCreator(self.pathred)
        img_conv.XDSINPCreator(self.pathsmv)
        self.logger.info("XDS INP file created.")

        if image_buffer:
            drc = os.path.join(self.path,"tiff_image")
            os.makedirs(drc)
            while len(image_buffer) != 0:
                i, img, h = image_buffer.pop(0)
                fn = os.path.join(drc, "{:05d}.tiff".format(i))
                write_tiff(fn, img, header=h)

        print "Data Collection and Conversion Done."
