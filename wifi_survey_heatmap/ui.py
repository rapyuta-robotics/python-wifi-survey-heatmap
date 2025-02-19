"""
The latest version of this package is available at:
<http://github.com/jantman/wifi-survey-heatmap>

##################################################################################
Copyright 2017 Jason Antman <jason@jasonantman.com> <http://www.jasonantman.com>

    This file is part of wifi-survey-heatmap, also known as wifi-survey-heatmap.

    wifi-survey-heatmap is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    wifi-survey-heatmap is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with wifi-survey-heatmap.  If not, see <http://www.gnu.org/licenses/>.

The Copyright and Authors attributions contained herein may not be removed or
otherwise altered, except to add the Author attribution of a contributor to
this work. (Additional Terms pursuant to Section 7b of the AGPL v3)
##################################################################################
While not legally required, I sincerely request that anyone who finds
bugs please submit them at <https://github.com/jantman/wifi-survey-heatmap> or
to me via email, and that you send any contributions or improvements
either as a pull request on GitHub, or to me via email.
##################################################################################

AUTHORS:
Jason Antman <jason@jasonantman.com> <http://www.jasonantman.com>
##################################################################################
"""

import sys
import argparse
import logging
import time
import wx
import json
import os
import subprocess
import rospy

from wifi_survey_heatmap.collector import Collector
from wifi_survey_heatmap.libnl import Scanner

FORMAT = "[%(asctime)s %(levelname)s] %(message)s"
logging.basicConfig(level=logging.WARNING, format=FORMAT)
logger = logging.getLogger()


RESULT_FIELDS = [
    'error',
    'time',
    'timesecs',
    'protocol',
    'num_streams',
    'blksize',
    'omit',
    'duration',
    'sent_bytes',
    'sent_bps',
    'received_bytes',
    'received_bps',
    'sent_kbps',
    'sent_Mbps',
    'sent_kB_s',
    'sent_MB_s',
    'received_kbps',
    'received_Mbps',
    'received_kB_s',
    'received_MB_s',
    'retransmits',
    'bytes',
    'bps',
    'jitter_ms',
    'kbps',
    'Mbps',
    'kB_s',
    'MB_s',
    'packets',
    'lost_packets',
    'lost_percent',
    'seconds'
]


class SurveyPoint(object):

    def __init__(self, parent, x, y):
        self.parent = parent
        self.x = x
        self.y = y
        self.is_finished = False
        self.is_failed = False
        self.progress = 0
        self.dotSize = 20
        self.result = {}

    def set_result(self, res):
        self.result = res

    @property
    def as_dict(self):
        return {
            'x': self.x,
            'y': self.y,
            'result': self.result,
            'failed': self.is_failed
        }

    def set_is_failed(self):
        self.is_failed = True
        self.progress = 0

    def set_progress(self, value, total):
        self.progress = int(100*value/total)

    def set_is_finished(self):
        self.is_finished = True
        self.is_failed = False
        self.progress = 100

    def draw(self, dc, color=None):
        if color is None:
            color = 'green'
            if not self.is_finished:
                color = 'orange'
            if self.is_failed:
                color = 'red'
        dc.SetPen(wx.Pen(color, style=wx.TRANSPARENT))
        dc.SetBrush(wx.Brush(color, wx.SOLID))

        # Relative scaling
        x = self.x / self.parent.scale_x
        y = self.y / self.parent.scale_y

        # Draw circle
        dc.DrawCircle(int(x), int(y), self.dotSize)

        # Put progress label on top of the circle
        dc.DrawLabel(
            "{}%".format(self.progress),
            wx.Rect(
                int(x-self.dotSize/2), int(y-self.dotSize/2),
                int(self.dotSize), int(self.dotSize)
            ),
            wx.ALIGN_CENTER
        )

    def erase(self, dc):
        """quicker than redrawing, since DC doesn't have persistence"""
        dc.SetPen(wx.Pen('white', style=wx.TRANSPARENT))
        dc.SetBrush(wx.Brush('white', wx.SOLID))
        # Relative scaling
        x = self.x / self.parent.scale_x
        y = self.y / self.parent.scale_y
        dc.DrawCircle(int(x), int(y), 1.1*self.dotSize)

    def includes_point(self, x, y):
        if (
            self.x - 20 <= x <= self.x + 20 and
            self.y - 20 <= y <= self.y + 20
        ):
            return True
        return False


class SafeEncoder(json.JSONEncoder):

    def default(self, obj):
        if isinstance(obj, type(b'')):
            return obj.decode()
        return json.JSONEncoder.default(self, obj)


class FloorplanPanel(wx.Panel):

    def __init__(self, parent, tcp_only = True):
        super(FloorplanPanel, self).__init__(parent)
        self.parent = parent
        self.img_path = parent.img_path
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)
        self.Bind(wx.EVT_LEFT_UP, self.onLeftUp)
        self.Bind(wx.EVT_LEFT_DOWN, self.onLeftDown)
        self.Bind(wx.EVT_MOTION, self.onMotion)
        self.Bind(wx.EVT_RIGHT_UP, self.onRightClick)
        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.survey_points = []
        self._moving_point = None
        self._moving_x = None
        self._moving_y = None
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.tcp_only = tcp_only
        self.data_filename = '%s.json' % self.parent.survey_title
        if os.path.exists(self.data_filename):
            self._load_file(self.data_filename)
        self._duration = self.parent.duration
        self.collector = Collector(
            self.parent.server, self._duration, self.parent.scanner)
        self.parent.SetStatusText("Ready.")

    def _load_file(self, fpath):
        with open(fpath, 'r') as fh:
            raw = fh.read()
        data = json.loads(raw)
        if 'survey_points' not in data:
            logger.error('Trying to load incompatible JSON file')
            exit(1)
        for point in data['survey_points']:
            p = SurveyPoint(self, point['x'], point['y'])
            p.set_result(point['result'])
            p.set_is_finished()
            self.survey_points.append(p)

    def OnEraseBackground(self, evt):
        """Add a picture to the background"""
        dc = evt.GetDC()
        if not dc:
            dc = wx.ClientDC(self)
            rect = self.GetUpdateRegion().GetBox()
            dc.SetClippingRect(rect)
        dc.Clear()

        # Get window size
        W, H = self.GetSize()

        # Load floorplan
        bmp = wx.Bitmap(self.img_path)
        image = wx.Bitmap.ConvertToImage(bmp)

        # Store scaling factors for pixel corrections
        self.scale_x = image.GetWidth() / W
        self.scale_y = image.GetHeight() / H

        # Scale image to window size
        logger.debug("Scaling image to {} x {}".format(W, H))
        image = image.Scale(W, H, wx.IMAGE_QUALITY_HIGH)

        # Draw image
        scaled_bmp = wx.Bitmap(image)
        dc.DrawBitmap(scaled_bmp, 0, 0)

    # Get X and Y coordinated scaled to ABSOLUTE coordinates of the floorplan
    def get_xy(self, event):
        X, Y = event.GetPosition()
        W, H = self.GetSize()
        x = int(X * self.scale_x)
        y = int(Y * self.scale_y)
        return [x, y]

    def onRightClick(self, event):
        x, y = self.get_xy(event)
        point = None
        for p in self.survey_points:
            # important to iterate the whole list, so we find the most recent
            if p.includes_point(x, y):
                point = p
        if point is None:
            self.parent.SetStatusText(
                f"No survey point found at ({x}, {y})"
            )
            self.Refresh()
            return
        # ok, we have a point to remove
        point.draw(wx.ClientDC(self), color='blue')
        res = self.YesNo(f'Remove point at ({x}, {y}) shown in blue?')
        if not res:
            self.parent.SetStatusText('Not removing point.')
            self.Refresh()
            return
        self.survey_points.remove(point)
        self.parent.SetStatusText(f'Removed point at ({x}, {y})')
        self.Refresh()
        self._write_json()

    def onLeftDown(self, event):
        x, y = self.get_xy(event)
        point = None
        for p in self.survey_points:
            # important to iterate the whole list, so we find the most recent
            if p.includes_point(x, y):
                point = p
        if point is None:
            self.parent.SetStatusText(
                f"No survey point found at ({x}, {y})"
            )
            self.Refresh()
            return
        self._moving_point = point
        self._moving_x = point.x
        self._moving_y = point.y
        point.draw(wx.ClientDC(self), color='lightblue')

    def onLeftUp(self, event):
        x, y = pos = self.get_xy(event)
        if self._moving_point is None:
            self._do_measurement(pos)
            return
        oldx = self._moving_point.x
        oldy = self._moving_point.y
        self._moving_point.x = x
        self._moving_point.y = y
        self._moving_point.draw(wx.ClientDC(self), color='lightblue')
        res = self.YesNo(
            f'Move point from ({oldx}, {oldy}) to ({x}, {y})?'
        )
        if not res:
            self._moving_point.x = self._moving_x
            self._moving_point.y = self._moving_y
        self._moving_point = None
        self._moving_x = None
        self._moving_y = None
        self.Refresh()
        self._write_json()

    def onMotion(self, event):
        if self._moving_point is None:
            return
        x, y = pos = self.get_xy(event)
        dc = wx.ClientDC(self)
        self._moving_point.erase(dc)
        self._moving_point.x = x
        self._moving_point.y = y
        self._moving_point.draw(dc, color='lightblue')

    def _check_bssid(self):
        # Return early if BSSID is not to be verified
        if self.parent.bssid is None:
            return True
        # Get BSSID from link
        bssid = self.collector.scanner.get_current_bssid()
        # Compare BSSID, exit early on match
        if bssid == self.parent.bssid:
            return True
        # Error logging
        logger.error(
            'Expected BSSID %s but found BSSID %s from kernel',
            self.parent.bssid, bssid
        )
        msg = f'ERROR: Expected BSSID {self.parent.bssid} but found ' \
              f'BSSID {bssid}'
        self.parent.SetStatusText(msg)
        self.Refresh()
        self.warn(msg)
        return False

    def _abort(self, reason):
        self.survey_points[-1].set_is_failed()
        self.parent.SetStatusText('Aborted: {}'.format(reason))
        self.Refresh()

    def _do_measurement(self, pos):
        rospy.loginfo('Starting survey...')
        # Add new survey point
        self.survey_points.append(SurveyPoint(self, pos[0], pos[1]))
        # Delete failed survey points
        self.survey_points = [p for p in self.survey_points if not p.is_failed]
        self.Refresh()
        res = {}
        count = 0
        # Number of steps in total (for the progress computation)
        steps = 5
        # Check if we are connected to an AP, all the
        # rest doesn't any sense otherwise
        if not self.collector.check_associated():
            self._abort("Not connected to an access point")
            return
        # Check BSSID
        if not self._check_bssid():
            self._abort("BSSID check failed")
            return

        # Skip iperf test if empty server string was given
        if self.collector._iperf_server is not None:
            for protoname, udp in {'tcp': False, 'udp': True}.items():
                if self.tcp_only and protoname == 'udp':
                    continue
                for suffix, reverse in {'': False, '-reverse': True}.items():
                    # Update progress mark
                    self.survey_points[-1].set_progress(count, steps)
                    count += 1



                    # Check if we're still connected to the same AP
                    if not self._check_bssid():
                        self._abort("BSSID check failed")
                        return

                    # Start iperf test
                    tmp = self.run_iperf(count, udp, reverse)
                    if tmp is None:
                        # bail out; abort this survey point
                        self._abort("iperf test failed")
                        return
                    # else success
                    res['%s%s' % (protoname, suffix)] = {
                        x: getattr(tmp, x, None) for x in RESULT_FIELDS
                    }
                    rospy.loginfo(
                        f'iperf at count {count} finished successfully')

        # Check if we're still connected to the same AP
        if not self._check_bssid():
            self._abort("BSSID check failed")
            return

        # Get all signal metrics from nl
        rospy.loginfo(
            'Getting signal metrics (Quality, signal strength, etc.)...')
        self.Refresh()
        data = self.collector.scanner.get_iface_data()
        # Merge dicts
        res = {**res, **data}
        self.survey_points[-1].set_progress(4, steps)

        # Scan APs in the neighborhood
        if self.parent.scan:
            rospy.loginfo(
                'Scanning all access points within reach...')
            self.Refresh()
            res['scan_results'] = self.collector.scan_all_access_points()
        self.survey_points[-1].set_progress(5, steps)

        # Save results and mark survey point as complete
        self.survey_points[-1].set_result(res)
        self.survey_points[-1].set_is_finished()
        rospy.loginfo(
            'Saving to: %s' % self.data_filename
        )
        self.Refresh()
        self._write_json()
        self._ding()

    def _ding(self):
        if self.parent.ding_path is None:
            return
        subprocess.call([self.parent.ding_command, self.parent.ding_path])

    def _write_json(self):
        # Only store finished survey points
        survey_points = [p.as_dict for p in self.survey_points if p.is_finished]

        res = json.dumps(
            {'img_path': self.img_path, 'survey_points': survey_points},
            cls=SafeEncoder, indent=2
        )
        with open(self.data_filename, 'w') as fh:
            fh.write(res)
        self.parent.SetStatusText(
            'Saved to %s; ready...' % self.data_filename
        )
        self.Refresh()

    def warn(self, message, caption='Warning!'):
        dlg = wx.MessageDialog(self.parent, message, caption,
                               wx.OK | wx.ICON_WARNING)
        dlg.ShowModal()
        dlg.Destroy()

    def YesNo(self, question, caption='Yes or no?'):
        dlg = wx.MessageDialog(self.parent, question, caption,
                               wx.YES_NO | wx.ICON_QUESTION)
        result = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        return result

    def run_iperf(self, count, udp, reverse):
        proto = "UDP" if udp else "TCP"
        # iperf3 default direction is uploading to the server
        direction = "Download" if reverse else "Upload"
        rospy.loginfo(
            'Running iperf %d/4: %s (%s) - takes %i seconds' % (count,
                                                                direction,
                                                                proto,
                                                                self._duration)
        )
        self.Refresh()
        tmp = self.collector.run_iperf(udp, reverse)
        time.sleep(2)
        if tmp.error is None:
            return tmp
        # else this is an error
        if tmp.error.startswith('unable to connect to server'):
            rospy.logerr(
                'ERROR: Unable to connect to iperf server at {}. Aborting.'.
                format(self.collector._iperf_server)
            )
            return None
        if self.YesNo('iperf error: %s. Retry?' % tmp.error):
            self.Refresh()
            return self.run_iperf(count, udp, reverse)
        # else bail out
        return tmp

    def on_paint(self, event=None):
        dc = wx.ClientDC(self)
        for p in self.survey_points:
            p.draw(dc)


class MainFrame(wx.Frame):

    def __init__(
            self, img_path, server, survey_title, scan, bssid, ding,
            ding_command, duration, scanner, *args, **kw
    ):
        super(MainFrame, self).__init__(*args, **kw)
        self.img_path = img_path
        self.server = server
        self.scan = scan
        self.survey_title = survey_title
        self.bssid = None
        if bssid:
            self.bssid = bssid.lower()
        self.ding_path = ding
        self.ding_command = ding_command
        self.duration = duration
        self.CreateStatusBar()
        self.scanner = scanner
        self.pnl = FloorplanPanel(self)
        self.makeMenuBar()

    def makeMenuBar(self):
        fileMenu = wx.Menu()
        fileMenu.AppendSeparator()
        exitItem = fileMenu.Append(wx.ID_EXIT)
        menuBar = wx.MenuBar()
        menuBar.Append(fileMenu, "&File")
        self.SetMenuBar(menuBar)
        self.Bind(wx.EVT_MENU, self.OnExit,  exitItem)

    def OnExit(self, event):
        """Close the frame, terminating the application."""
        self.Close(True)


def parse_args(argv):
    """
    parse arguments/options

    this uses the new argparse module instead of optparse
    see: <https://docs.python.org/2/library/argparse.html>
    """
    p = argparse.ArgumentParser(description='wifi survey data collection UI')
    p.add_argument('-v', '--verbose', dest='verbose', action='count', default=0,
                   help='verbose output. specify twice for debug-level output.')
    p.add_argument('-S', '--scan', dest='scan', action='store_true',
                   default=False, help='Scan for access points in the vicinity')
    p.add_argument('-s', '--server', dest='IPERF3_SERVER', action='store', type=str,
                   default=None, help='iperf3 server IP or hostname')
    p.add_argument('-d', '--duration', dest='IPERF3_DURATION', action='store',
                   type=int, default=10,
                   help='Duration of each individual ipref3 test run')
    p.add_argument('-b', '--bssid', dest='BSSID', action='store', type=str,
                   default=None, help='Restrict survey to this BSSID')
    p.add_argument('--ding', dest='ding', action='store', type=str,
                   default=None,
                   help='Path to audio file to play when measurement finishes')
    p.add_argument('--ding-command', dest='ding_command', action='store',
                   type=str, default='/usr/bin/paplay',
                   help='Path to ding command')
    p.add_argument('-i', '--interface', dest='INTERFACE', action='store',
                   type=str, default=None,
                   help='Wireless interface name')
    p.add_argument('-p', '--picture', dest='IMAGE', type=str,
                   default=None, help='Path to background image')
    p.add_argument('-t', '--title', dest='TITLE', type=str,
                   default=None, help='Title for survey (and data filename)'
                   )
    p.add_argument('--libnl-debug', dest='libnl_debug', action='store_true',
                   default=False,
                   help='enable debug-level logging for libnl')
    args = p.parse_args(argv)
    return args


def set_log_info():
    """set logger level to INFO"""
    set_log_level_format(logging.INFO,
                         '%(asctime)s %(levelname)s:%(name)s:%(message)s')


def set_log_debug():
    """set logger level to DEBUG, and debug-level output format"""
    set_log_level_format(
        logging.DEBUG,
        "%(asctime)s [%(levelname)s %(filename)s:%(lineno)s - "
        "%(name)s.%(funcName)s() ] %(message)s"
    )


def set_log_level_format(level, format):
    """
    Set logger level and format.

    :param level: logging level; see the :py:mod:`logging` constants.
    :type level: int
    :param format: logging formatter format string
    :type format: str
    """
    formatter = logging.Formatter(fmt=format)
    logger.handlers[0].setFormatter(formatter)
    logger.setLevel(level)


def ask_for_wifi_iface(app, scanner):
    frame = wx.Frame(None)
    title = 'Wireless interface'
    description = 'Please specify the wireless interface\nto be used for your survey'
    dlg = wx.SingleChoiceDialog(frame, description, title, scanner.iface_names)
    if dlg.ShowModal() == wx.ID_OK:
        resu = dlg.GetStringSelection()
    else:
        # User clicked [Cancel]
        exit()
    dlg.Destroy()
    frame.Destroy()

    return resu


def ask_for_title(app):
    frame = wx.Frame(None)
    title = 'Title of your measurement'
    description = 'Please specify a title for your measurement. This title will be used to store the results and to distinguish the generated plots'
    default = 'Example'
    dlg = wx.TextEntryDialog(frame, description, title)
    dlg.SetValue(default)
    if dlg.ShowModal() == wx.ID_OK:
        resu = dlg.GetValue()
    else:
        # User clicked [Cancel]
        exit()
    dlg.Destroy()
    frame.Destroy()

    return resu


def ask_for_floorplan(app):
    frame = wx.Frame(None)
    title = 'Select floorplan for your measurement'
    dlg = wx.FileDialog(frame, title,
                        wildcard='Compatible image files (*.png, *.jpg,*.tiff, *.bmp)|*.png;*.jpg;*.tiff;*.bmp;*:PNG;*.JPG;*.TIFF;*.BMP;*.jpeg;*.JPEG',
                        style=wx.FD_FILE_MUST_EXIST)
    if dlg.ShowModal() == wx.ID_OK:
        resu = dlg.GetPath()
        print(resu)
    else:
        # User clicked [Cancel]
        exit()
    dlg.Destroy()
    frame.Destroy()

    return resu


def main():
    if os.getuid() != 0:
        logger.warning("You should run this script as root"
                       " to be able to trigger Wi-Fi scans.")

    # Parse input arguments
    args = parse_args(sys.argv[1:])

    # set logging level
    if args.verbose > 1:
        set_log_debug()
    elif args.verbose == 1:
        set_log_info()

    if not args.libnl_debug:
        for lname in ['libnl']:
            log = logging.getLogger(lname)
            log.setLevel(logging.WARNING)
            log.propagate = True

    app = wx.App()

    scanner = Scanner(scan=args.scan)

    # Ask for possibly missing fields
    # Wireless interface
    if args.INTERFACE is None:
        INTERFACE = ask_for_wifi_iface(app, scanner)
    else:
        INTERFACE = args.INTERFACE

    # Definitely set interface at this point
    scanner.set_interface(INTERFACE)

   # Floorplan image
    if args.IMAGE is None:
        IMAGE = ask_for_floorplan(app)
    else:
        IMAGE = args.IMAGE

    # Title
    if args.TITLE is None:
        TITLE = ask_for_title(app)
    else:
        TITLE = args.TITLE

    frm = MainFrame(
        IMAGE, args.IPERF3_SERVER, TITLE, args.scan,
        args.BSSID, args.ding, args.ding_command, args.IPERF3_DURATION,
        scanner, None, title='wifi-survey: %s' % args.TITLE,
    )
    frm.Show()
    frm.SetStatusText('%s' % frm.pnl.GetSize())
    app.MainLoop()


if __name__ == '__main__':
    main()
