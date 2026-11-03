"""Builds data/areacode_zones.csv and data/zones.txt from libphonenumber map_data.txt and geocoding/en/1.txt.
Usage: python3 -I build_zones.py <dir with map_data.txt and geocoding_en_1.txt> <out dir>"""
import csv
import os
import sys

CANADA = {"Ontario", "Quebec", "British Columbia", "British Colombia", "Alberta", "Saskatchewan", "Manitoba",
          "Nova Scotia/Prince Edward Island", "Newfoundland and Labrador", "New Brunswick",
          "Northwest Territories/Nunavut/Yukon", "Toronto, ON", "Montreal, QC"}
# no geocoding line at 3-digit level; classified by hand (NANPA assignments)
HAND = {"203": "US", "713": "US", "718": "US", "905": "CA", "340": "US-territory", "670": "US-territory",
        "671": "US-territory", "684": "US-territory", "787": "US-territory", "939": "US-territory"}
SUPPLEMENT = ["America/Indiana/Indianapolis", "America/Indiana/Knox", "America/Kentucky/Louisville", "America/Detroit",
              "America/Whitehorse", "America/Iqaluit", "America/Moncton"]


def main(src, out):
    geo = {}
    for line in open(os.path.join(src, "geocoding_en_1.txt"), encoding="utf-8"):
        if line.startswith("#") or "|" not in line:
            continue
        k, v = line.rstrip("\n").split("|", 1)
        if len(k) == 4 and k[0] == "1":
            geo[k[1:]] = v
    codes, zones = {}, set()
    for line in open(os.path.join(src, "map_data.txt"), encoding="utf-8"):
        if line.startswith("#") or "|" not in line:
            continue
        k, v = line.rstrip("\n").split("|", 1)
        if not k.startswith("1") or k == "1":
            continue
        zs = v.split("&")
        zones.update(zs)
        if len(k) >= 4:
            codes.setdefault(k[1:4], set()).update(zs)
    rows = []
    for ac in sorted(codes):
        g = geo.get(ac, "")
        label = HAND.get(ac) or ("CA" if g in CANADA else ("US" if g else "other-NANP"))
        rows.append((ac, label, g, "&".join(sorted(codes[ac]))))
    with open(os.path.join(out, "areacode_zones.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["area_code", "label", "geocoding_name", "zones"])
        w.writerows(rows)
    allz = sorted(zones) + [z for z in SUPPLEMENT if z not in zones]
    with open(os.path.join(out, "zones.txt"), "w", encoding="utf-8") as f:
        for z in allz:
            f.write(("%s\t%s\n" % (z, "dataset" if z in zones else "supplement")))
    lab = {}
    for r in rows:
        lab[r[1]] = lab.get(r[1], 0) + 1
    print("area codes:", len(rows), lab)
    print("zones from dataset:", len(zones), "supplement:", len([z for z in SUPPLEMENT if z not in zones]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
