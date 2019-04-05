import pyautogui as pg
import pygetwindow as pw
from instamatic.utils.singleton import Singleton
from pathlib import Path
from instamatic import config
from contextlib import contextmanager


class EMMenuError(Exception):
    pass


class EMMenuWrapper(object):
    """Small wrapper the EMMenu gui for automating data collection"""
    instance = None

    def __init__(self, name="emmenu"):
        super(EMMenuWrapper, self).__init__()

        self.name = name
        self._switch_back = True

        self.load_defaults()

        curdir = Path(__file__).parent

        self._path_start_liveview_button = str(curdir / "emmenu" / "start_liveview.png")
        self._path_start_record_button   = str(curdir / "emmenu" / "start_record.png")
        self._path_stop_liveview_button1 = str(curdir / "emmenu" / "stop_liveview1.png")
        self._path_stop_liveview_button2 = str(curdir / "emmenu" / "stop_liveview2.png")
        self._path_acquire_button        = str(curdir / "emmenu" / "acquire.png")

        self.win_previous = None
        windows = pw.getWindowsWithTitle("EMMENU4")

        if len(windows) == 0:
            raise EMMenuError("Could not find EMMENU, is it running?")
        if len(windows) > 1:
            for title in pw.getAllTitles():
                if title:
                    print(title)
            raise EMMenuError("Found more than one instance of EMMENU -> ???")

        self.win_emmenu = windows[0]
        
        self.locate_buttons()

    def load_defaults(self):
        if self.name != config.cfg.camera:
            config.load(camera_name=self.name)

        self.__dict__.update(config.camera.d)

        self.streamable = False

    def locate_buttons(self):
        """Locate the buttons to start/stop recording/live view"""
        self.activate()

        record_button_pos = pg.locateCenterOnScreen(self._path_start_record_button, grayscale=True)
        if not record_button_pos:
            raise EMMenuError("Could not locate record view button")
        self.record_button_pos = record_button_pos
        
        # attempt 1, liveview is running
        liveview_button_pos = pg.locateOnScreen(self._path_stop_liveview_button1, grayscale=True)
        if not liveview_button_pos:
            # attempt 2, liveview is not running
            liveview_button_pos = pg.locateOnScreen(self._path_start_liveview_button, grayscale=True)
            if not liveview_button_pos:
                # attempt 3, liveview is running, but deselected
                liveview_button_pos = pg.locateOnScreen(self._path_stop_liveview_button2, grayscale=True)
                if not liveview_button_pos:
                    raise EMMenuError("Could not locate live view button")
        
        self.liveview_button_pos = pg.center(liveview_button_pos)

        acquire_button_pos = pg.locateCenterOnScreen(self._path_acquire_button, grayscale=True)
        if not acquire_button_pos:
            raise EMMenuError("Could not locate record view button")
        self.acquire_button_pos = acquire_button_pos

        print(f"Record button position: {self.record_button_pos}")
        print(f"Liveview button position: {self.liveview_button_pos}")
        print(f"Acquire button position: {self.acquire_button_pos}")

        self.activate_previous()

    def getImage(self, **kwargs):
        """Hi-jack `getImage` to display data collection warning."""
        raise IOError(f"{self.__class__.__name__} does not support `getImage`. Use `ctrl.cam.acquire` or `ctrl.cam.record` instead. The images will be stored in the EMMENU buffer.")

    def acquire(self):
        """
        Collect image on the camera using the currently selected camera in EMMENU. 
        The image is stored in the EMMENU buffer.
        """
        self._press(self.acquire_button_pos)

    def start_record(self):
        """Start recording the current live view of the currently selected camera. The data are saved in the EMMENU buffer."""
        self.toggle_record() 

    def stop_record(self):
        """Stop the recording."""
        self.toggle_liveview()

    @property
    def live_view_is_running(self):
        """Return `True` if the live view is running"""
        self.activate()

        if pg.locateOnScreen(self._path_stop_liveview_button1, grayscale=True):
            ret = True
        elif pg.locateOnScreen(self._path_stop_liveview_button2, grayscale=True):
            ret = True
        else:
            ret = False

        self.activate_previous()
        
        return ret

    @property
    def is_active(self):
        """Return `True` if EMMENU is the active window"""
        return self.win_emmenu.isActive

    def activate(self):
        """Active the EMMENU4 window"""
        if not self.is_active:
            self.win_previous = pw.getActiveWindow()
        self.win_emmenu.activate()
        self.win_emmenu.maximize()

    def activate_previous(self):
        """Go back to the previous active window after `self.activate` has been used"""
        if self.win_previous:
            self.win_previous.activate()

    def _press(self, button_loc):
        """Toggle the liveview button, and switch back to the currently active window"""
        switch_back = False
        current_loc = pg.position()
        if not self.is_active:
            self.activate()
            switch_back = True
        pg.moveTo(button_loc)
        pg.click()
        pg.moveTo(current_loc)
        if switch_back:
            self.activate_previous()

    def toggle_record(self):
        """Toggle the record button, and switch back to the currently active window"""
        self._press(self.record_button_pos)

    def toggle_liveview(self):
        """Toggle the liveview button, and switch back to the currently active window"""
        self._press(self.liveview_button_pos)

    @contextmanager
    def keep_in_focus(self):
        """Keep the EMMENU window in focus while executing a few clicks in a row to prevent windows flashing"""
        was_active = self.is_active
        self._switch_back = False
        yield
        self._switch_back = True
        if not was_active:
            self.activate_previous()
