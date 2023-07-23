
import sys
import os

sys.path.append("~/catkin_ws/src/rr_oks/oks_wifi_analyzer/src/")
sys.path.append(os.getcwd())
import wifi_survey_heatmap.libnl as nl

# main func
def main():
    s = nl.Scanner(interface_name="wlp0s20f3", scan=True, restricted=True, concise=True)
    s.scan_all_access_points()

if __name__ == "__main__":
    main()