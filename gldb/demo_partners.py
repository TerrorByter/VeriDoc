"""
gldb/demo_partners.py
---------------------
Who each demo company trades with. The team's sample statement has seven
trades: five goods purchases and two sales, in this order. ACMA's are the ones
the team's statement names; every other company has its own customers and
suppliers in its own line of business at the same places on its statement,
so no two companies share a counterparty, a product or a number.

A customer is a buyer in Malaysia the company exports to by sea; a supplier is
a company in Singapore, registered at ACRA, that delivers to it (see
gldb.demo_trade). Each supplier banks where it chooses, not all at one branch.
"""

from __future__ import annotations

from decimal import Decimal

from gldb.demo_trade import Partner
from gldb.trade_pdf import Packing

_SALES = "Sales Revenue"


def _customer(name: str, address: tuple[str, str], goods: tuple[str, str], quantity: int, packing: Packing,
              code: str, port: str) -> Partner:
    return Partner(name, address, _SALES, goods, quantity, packing, code, port=port)


def _supplier(name: str, address: tuple[str, str], paid_for: str, goods: tuple[str, str], quantity: int,
              packing: Packing, code: str, uen: str, incorporated: str, bank: str, account: str,
              director: tuple[str, str]) -> Partner:
    return Partner(name, address, paid_for, goods, quantity, packing, code, uen=uen, incorporated=incorporated,
                   bank=bank, account=account, director=director)


def _pack(kind: str, per_package: int, kg: str, cbm: str) -> Packing:
    return Packing(kind, per_package, Decimal(kg), Decimal(cbm))


# ACMA LTD.: the team's sample, which sells air-handling and chilled-water units
ACMA = (
    _supplier("ABC electronics Ltd.", ("30 Pioneer Crescent, #02-11", "Singapore 628559"), "Electronic Supplies",
              ("Electronic components, assorted", "Freight and handling"), 500, _pack("CARTONS", 25, "0.8", "0.004"),
              "ABC", "199804567H", "1998-03-12", "First Citizens Bank, Jurong East Branch", "201-118-402-557",
              ("LEONG CHEE KEONG", "S6402345D")),
    _customer("EFG Company", ("Lot 23, Jalan Perigi Nanas 6/1, Pulau Indah Industrial Park",
                              "42920 Pelabuhan Klang, Selangor, Malaysia"),
              ("Air-handling unit AHU-450", "Commissioning kit and spares"), 40, _pack("CRATES", 1, "850", "6.5"),
              "EFG", "Port Klang"),
    _supplier("SteelMen Pte Ltd.", ("12 Tuas Avenue 10", "Singapore 639142"), "Steel Sheets",
              ("Cold-rolled steel sheets, 3 mm", "Cutting and delivery"), 120, _pack("BUNDLES", 10, "73.6", "0.0094"),
              "SM", "200709981R", "2007-06-04", "Straits Commercial Bank, Tuas Branch", "202-663-018-340",
              ("CHUA HOCK SENG", "S6911234F")),
    _supplier("Cool Robotics Ltd.", ("2 Science Park Drive, #05-02", "Singapore 118222"), "Factory Machine",
              ("Robotic arm assembly unit", "Installation and commissioning"), 2, _pack("CRATES", 1, "1450", "4.2"),
              "CR", "201511222G", "2015-11-19", "Harbourfront Merchant Bank, Buona Vista Branch", "203-770-551-216",
              ("RAJESH MENON", "S7412345H")),
    _supplier("Caterpillarz Inc.", ("10 Jalan Besar, #04-17", "Singapore 208787"), "Forklifts",
              ("Forklift, 3-tonne diesel", "Delivery and registration"), 4, _pack("UNITS", 1, "4600", "11.5"),
              "CAT", "T98FC1234C", "1998-09-01", "Straits Commercial Bank, Bugis Branch", "204-082-947-663",
              ("DAVID HOLLIS", "S8503456J")),
    _customer("Metallica Inc.", ("No. 8, Jalan Tembaga, Kawasan Perindustrian Perai", "13600 Perai, Pulau Pinang, Malaysia"),
              ("Chilled-water unit CWU-900", "Installation and commissioning"), 25, _pack("CRATES", 1, "1900", "9.8"),
              "MTL", "Penang"),
    _supplier("Tan & Co.", ("5 Woodlands Industrial Park D, #01-06", "Singapore 757919"), "Air Conditioner Supplies",
              ("Air-conditioner compressors and coils", "Delivery and servicing"), 60, _pack("PALLETS", 4, "38", "0.12"),
              "TC", "53123456K", "2009-05-21", "First Citizens Bank, Woodlands Branch", "205-419-836-770",
              ("TAN KAH WEI", "S7101234L")),
)

# BRIGHTWAVE COMPONENTS PTE. LTD.: makes LED driver modules
BRIGHTWAVE = (
    _supplier("Kallang Microchip Pte. Ltd.", ("8 Kallang Sector, #05-03", "Singapore 349282"), "Semiconductors",
              ("Power MOSFET chips, reel of 5,000", "Freight and handling"), 60, _pack("CARTONS", 10, "2.5", "0.01"),
              "KMC", "201012876W", "2010-07-14", "Harbourfront Merchant Bank, Kallang Branch", "311-204-775-019",
              ("YEO CHIN HWEE", "S7208813B")),
    _customer("Sinar Lumina Sdn. Bhd.", ("Lot 5, Jalan Pelabur 23/1, Seksyen 23", "40300 Shah Alam, Selangor, Malaysia"),
              ("LED driver module LD-60", "Firmware programming and testing"), 12000,
              _pack("CARTONS", 100, "0.12", "0.0008"), "SLM", "Port Klang"),
    _supplier("Ubi PCB Works Pte. Ltd.", ("53 Ubi Avenue 1, #02-29", "Singapore 408934"), "Circuit Boards",
              ("Printed circuit boards, 4-layer", "Tooling and stencils"), 15000, _pack("CARTONS", 250, "0.05", "0.0002"),
              "UPW", "201403318E", "2014-02-03", "First Citizens Bank, Ubi Branch", "312-551-030-648",
              ("NG BOON CHYE", "S6805521J")),
    _supplier("Precision Reflow Systems Pte. Ltd.", ("21 Toh Guan Road East, #08-11", "Singapore 608609"),
              "Factory Machine", ("Reflow soldering oven RS-8", "Installation and calibration"), 1,
              _pack("CRATES", 1, "2200", "9.5"), "PRS", "200816604M", "2008-09-22",
              "Straits Commercial Bank, Jurong Branch", "313-087-264-905", ("HENG SIEW LIAN", "S6712093G")),
    _supplier("Meridian Instruments Pte. Ltd.", ("3 Kaki Bukit Crescent, #04-02", "Singapore 416237"), "Test Equipment",
              ("Automated optical inspection unit", "Training and setup"), 2, _pack("CRATES", 1, "680", "3.1"),
              "MRI", "201720045H", "2017-05-30", "Harbourfront Merchant Bank, Paya Lebar Branch", "314-662-118-237",
              ("ARVIND PILLAI", "S7934410C")),
    _customer("Penang Brightlite Sdn. Bhd.", ("Plot 102, Lorong Perusahaan Maju 7, Bayan Lepas FIZ",
                                             "11900 Bayan Lepas, Pulau Pinang, Malaysia"),
              ("LED driver module LD-120", "Firmware programming and testing"), 8000,
              _pack("CARTONS", 100, "0.18", "0.0011"), "PBL", "Penang"),
    _supplier("Jurong Passive Parts Pte. Ltd.", ("17 Jurong Port Road, #03-08", "Singapore 619092"), "Component Supplies",
              ("Capacitors and resistors, assorted reels", "Freight and handling"), 400, _pack("CARTONS", 20, "1.2", "0.004"),
              "JPP", "201109937N", "2011-04-11", "First Citizens Bank, Jurong East Branch", "315-930-472-081",
              ("LOW KAH MENG", "S7310462E")),
)

# CORVID MARINE ENGINEERING PTE. LTD.: builds marine pumps and repairs ships
CORVID = (
    _supplier("Pioneer Valve & Fittings Pte. Ltd.", ("45 Pioneer Road, #01-12", "Singapore 639598"), "Marine Valves",
              ("Butterfly valve DN300, marine grade", "Pressure testing"), 80, _pack("CRATES", 4, "95", "0.15"),
              "PVF", "200605512C", "2006-03-08", "Straits Commercial Bank, Tuas Branch", "321-418-209-663",
              ("SOH KENG LEONG", "S6601877H")),
    _customer("Samudera Dockyard Sdn. Bhd.", ("PLO 12, Jalan Keluli 7, Kawasan Perindustrian Pasir Gudang",
                                             "81700 Pasir Gudang, Johor, Malaysia"),
              ("Marine centrifugal pump MCP-200", "Commissioning and sea trial support"), 30,
              _pack("CRATES", 1, "640", "1.4"), "SDY", "Pasir Gudang"),
    _supplier("Tuas Steel Merchants Pte. Ltd.", ("31 Tuas View Crescent", "Singapore 637611"), "Steel Plates",
              ("Shipbuilding steel plate, 12 mm", "Cutting and delivery"), 150, _pack("BUNDLES", 5, "950", "0.12"),
              "TSM", "199907321K", "1999-08-17", "First Citizens Bank, Jurong East Branch", "322-705-336-418",
              ("LAU TECK SOON", "S6203318A")),
    _supplier("Gul Welding Technologies Pte. Ltd.", ("12 Gul Lane", "Singapore 629409"), "Workshop Machine",
              ("Submerged-arc welding system", "Installation and training"), 2, _pack("CRATES", 1, "1100", "4.2"),
              "GWT", "201209118D", "2012-03-26", "Harbourfront Merchant Bank, Jurong Branch", "323-146-807-552",
              ("MOHAMED RIDZWAN", "S7605124F")),
    _supplier("Benoi Coatings Pte. Ltd.", ("8 Benoi Sector, #02-05", "Singapore 629842"), "Marine Coatings",
              ("Anti-fouling paint, 20 L drum", "Delivery"), 400, _pack("PALLETS", 16, "26", "0.03"),
              "BNC", "201316730G", "2013-10-02", "Straits Commercial Bank, Boon Lay Branch", "324-590-118-276",
              ("ANG SOK HOON", "S7208345I")),
    _customer("Kuantan Offshore Marine Sdn. Bhd.", ("Lot 9, Jalan Pelabuhan, Kawasan Perindustrian Gebeng",
                                                   "26080 Kuantan, Pahang, Malaysia"),
              ("Marine centrifugal pump MCP-350", "Commissioning and sea trial support"), 18,
              _pack("CRATES", 1, "980", "2.1"), "KOM", "Kuantan"),
    _supplier("Loyang Engineering Supplies Pte. Ltd.", ("20 Loyang Crescent, #01-15", "Singapore 509016"),
              "Pump Components", ("Impellers and pump casings", "Machining"), 50, _pack("CRATES", 2, "210", "0.4"),
              "LES", "200411263B", "2004-11-15", "First Citizens Bank, Pasir Ris Branch", "325-274-063-991",
              ("KWEK HAN LIM", "S6411506D")),
)

# DUSKFIELD TRADING PTE. LTD.: trades tiles and sanitaryware
DUSKFIELD = (
    _supplier("Kranji Ceramics Pte. Ltd.", ("31 Kranji Loop, #02-01", "Singapore 739570"), "Tiles",
              ("Porcelain floor tiles 600x600, box", "Delivery"), 4000, _pack("PALLETS", 40, "26", "0.02"),
              "KCR", "201006254A", "2010-06-21", "Straits Commercial Bank, Woodlands Branch", "331-602-541-873",
              ("TAY BEE LENG", "S7006418B")),
    _customer("Bina Jaya Hardware Sdn. Bhd.", ("No. 15, Jalan Industri USJ 1/6", "47600 Subang Jaya, Selangor, Malaysia"),
              ("Vitreous china sanitaryware set", "Handling and documentation"), 6000, _pack("PALLETS", 8, "38", "0.25"),
              "BJH", "Port Klang"),
    _supplier("Senoko Bathroom Supplies Pte. Ltd.", ("6 Senoko Drive, #03-02", "Singapore 758200"), "Sanitaryware",
              ("Vitreous china sanitaryware set", "Delivery"), 1500, _pack("PALLETS", 8, "38", "0.25"),
              "SBS", "201508842K", "2015-08-11", "First Citizens Bank, Woodlands Branch", "332-118-904-526",
              ("PANG WEI XIANG", "S8102267H")),
    _supplier("Pandan Storage Systems Pte. Ltd.", ("19 Pandan Loop, #01-03", "Singapore 128232"), "Warehouse Racking",
              ("Selective pallet racking bay", "Installation"), 60, _pack("BUNDLES", 4, "180", "0.6"),
              "PSS", "200913377J", "2009-12-07", "Harbourfront Merchant Bank, Clementi Branch", "333-460-217-385",
              ("SURESH KUMAR", "S6908831C")),
    _supplier("Sungei Kadut Forklift Pte. Ltd.", ("22 Sungei Kadut Avenue", "Singapore 729665"), "Forklifts",
              ("Forklift, 2.5-tonne electric", "Delivery and registration"), 3, _pack("UNITS", 1, "4100", "10.5"),
              "SKF", "200302871M", "2003-04-14", "Straits Commercial Bank, Kranji Branch", "334-873-602-149",
              ("FOO KOK WAH", "S5908846G")),
    _customer("Seri Kota Building Supply Sdn. Bhd.", ("No. 3, Jalan Tanjung Api, Kawasan Perindustrian Semambu",
                                                     "25350 Kuantan, Pahang, Malaysia"),
              ("Porcelain floor tiles 600x600, box", "Handling and documentation"), 30000, _pack("PALLETS", 40, "26", "0.02"),
              "SKB", "Kuantan"),
    _supplier("Changi Tile & Stone Pte. Ltd.", ("7 Changi South Street 2, #02-04", "Singapore 486415"),
              "Building Materials", ("Granite slabs, 20 mm", "Cutting and delivery"), 900, _pack("PALLETS", 10, "75", "0.06"),
              "CTS", "201211096F", "2012-05-28", "First Citizens Bank, Bedok Branch", "335-209-748-310",
              ("ISMAIL BIN HASSAN", "S6705219D")),
)

# EASTGATE HOLDINGS PTE. LTD.: distributes office furniture
EASTGATE = (
    _supplier("Kallang Seating Pte. Ltd.", ("1 Kallang Junction, #04-09", "Singapore 339263"), "Office Chairs",
              ("Ergonomic office chair EC-5", "Delivery"), 900, _pack("CARTONS", 1, "19", "0.25"),
              "KSE", "201110483B", "2011-03-15", "First Citizens Bank, Kallang Branch", "341-306-829-457",
              ("CHEW LI TING", "S8207734J")),
    _customer("Mewah Office Interiors Sdn. Bhd.", ("Unit 3-1, Jalan PJU 7/16A, Mutiara Damansara",
                                                  "47800 Petaling Jaya, Selangor, Malaysia"),
              ("Modular workstation, 4-seat cluster", "Assembly drawings and packing"), 500,
              _pack("CRATES", 1, "210", "1.6"), "MOI", "Port Klang"),
    _supplier("Tuas Metalworks Pte. Ltd.", ("9 Tuas Avenue 5", "Singapore 639337"), "Steel Frames",
              ("Powder-coated steel desk frame", "Delivery"), 2000, _pack("BUNDLES", 10, "22", "0.05"),
              "TMW", "200710255H", "2007-09-03", "Straits Commercial Bank, Tuas Branch", "342-517-093-684",
              ("GOH TECK CHUAN", "S6510983E")),
    _supplier("Woodlands CNC Pte. Ltd.", ("11 Woodlands Close, #05-21", "Singapore 737853"), "Factory Machine",
              ("CNC panel saw", "Installation and training"), 1, _pack("CRATES", 1, "1800", "8"),
              "WCN", "201604417C", "2016-04-18", "Harbourfront Merchant Bank, Woodlands Branch", "343-028-651-972",
              ("NORAZLAN BIN OMAR", "S7808124A")),
    _supplier("Defu Laminates Pte. Ltd.", ("25 Defu Lane 10, #02-114", "Singapore 539214"), "Panels",
              ("Laminated desk panel 1600 mm", "Delivery"), 2500, _pack("PALLETS", 50, "28", "0.04"),
              "DFL", "200508921E", "2005-07-04", "First Citizens Bank, Hougang Branch", "344-781-206-538",
              ("TEO SIEW KIM", "S6309712B")),
    _customer("Johor Workspace Solutions Sdn. Bhd.", ("No. 22, Persiaran Teknologi, Taman Teknologi Johor",
                                                     "81400 Senai, Johor, Malaysia"),
              ("Modular workstation, 6-seat cluster", "Assembly drawings and packing"), 300,
              _pack("CRATES", 1, "300", "2.3"), "JWS", "Pasir Gudang"),
    _supplier("Bedok Hardware Trading Pte. Ltd.", ("3 Bedok North Street 5, #01-14", "Singapore 486131"),
              "Furniture Fittings", ("Cable trays and fittings, set", "Delivery"), 3000, _pack("CARTONS", 20, "3.5", "0.01"),
              "BHT", "199603528G", "1996-06-24", "Straits Commercial Bank, Bedok Branch", "345-962-430-817",
              ("LIEW CHOON HOCK", "S5807365F")),
)

# FERNLEAF SUPPLIES PTE. LTD.: supplies nurseries and landscapers
FERNLEAF = (
    _supplier("Lim Chu Kang Agro Pte. Ltd.", ("150 Neo Tiew Road", "Singapore 719004"), "Fertiliser",
              ("Slow-release fertiliser, 25 kg bag", "Delivery"), 3000, _pack("PALLETS", 40, "25", "0.03"),
              "LCK", "200908712D", "2009-09-01", "First Citizens Bank, Choa Chu Kang Branch", "351-204-638-791",
              ("WEE AH HUAT", "S5511027C")),
    _customer("Hijau Landskap Sdn. Bhd.", ("No. 7, Jalan Nusa Bestari 2/1, Taman Nusa Bestari",
                                          "81300 Skudai, Johor, Malaysia"),
              ("Potting mix, 50 L bag", "Handling and documentation"), 40000, _pack("PALLETS", 60, "18", "0.06"),
              "HLS", "Pasir Gudang"),
    _supplier("Sungei Tengah Pottery Pte. Ltd.", ("21 Sungei Tengah Road", "Singapore 699013"), "Planters",
              ("Fibreglass planter, 80 cm", "Delivery"), 1200, _pack("PALLETS", 6, "14", "0.35"),
              "STP", "201306654H", "2013-06-17", "Harbourfront Merchant Bank, Bukit Panjang Branch", "352-637-019-284",
              ("HO MEI FONG", "S7012268I")),
    _supplier("Kranji Agritech Pte. Ltd.", ("8 Kranji Way, #01-02", "Singapore 739446"), "Greenhouse Equipment",
              ("Automated irrigation controller", "Installation"), 20, _pack("CARTONS", 1, "12", "0.05"),
              "KAT", "201807731K", "2018-07-23", "Straits Commercial Bank, Kranji Branch", "353-918-402-675",
              ("VIJAY RAMAN", "S8410935B")),
    _supplier("Pasir Panjang Motors Pte. Ltd.", ("75 Pasir Panjang Road", "Singapore 118506"), "Delivery Lorry",
              ("Light lorry, 3-tonne", "Registration"), 1, _pack("UNITS", 1, "3500", "18"),
              "PPM", "199209844E", "1992-10-12", "First Citizens Bank, Pasir Panjang Branch", "354-150-863-027",
              ("SIM CHENG GUAN", "S5602481A")),
    _customer("Taman Seri Nursery Sdn. Bhd.", ("Lot 1288, Jalan Sungai Buloh", "47000 Sungai Buloh, Selangor, Malaysia"),
              ("Potting mix, 50 L bag", "Handling and documentation"), 36000, _pack("PALLETS", 60, "18", "0.06"),
              "TSN", "Port Klang"),
    _supplier("Neo Tiew Soil Supplies Pte. Ltd.", ("40 Neo Tiew Crescent", "Singapore 718856"), "Growing Media",
              ("Coco peat block, 5 kg", "Delivery"), 20000, _pack("PALLETS", 200, "5", "0.008"),
              "NTS", "201402267J", "2014-02-10", "Harbourfront Merchant Bank, Lim Chu Kang Branch", "355-482-130-596",
              ("JASMINE KOH", "S8606314F")),
)

# OAKRIDGE FABRICATION PTE. LTD.: fabricates structural steel
OAKRIDGE = (
    _supplier("Benoi Weld Consumables Pte. Ltd.", ("17 Benoi Crescent", "Singapore 629979"), "Welding Supplies",
              ("Welding wire, 15 kg spool", "Delivery"), 1500, _pack("PALLETS", 48, "15", "0.01"),
              "BWC", "201114092M", "2011-11-07", "Straits Commercial Bank, Boon Lay Branch", "361-725-904-318",
              ("TOH KIAN SENG", "S6807149J")),
    _customer("Gebeng Steelworks Sdn. Bhd.", ("Lot 45, Kawasan Perindustrian Gebeng", "26080 Kuantan, Pahang, Malaysia"),
              ("Fabricated steel truss, 12 m", "Shop drawings and inspection"), 60, _pack("UNITS", 1, "1450", "6"),
              "GSW", "Kuantan"),
    _supplier("Jurong Steel Stockists Pte. Ltd.", ("6 Jurong Pier Road", "Singapore 619158"), "Steel Sections",
              ("Universal beam 305x165, 12 m", "Cutting and delivery"), 200, _pack("BUNDLES", 5, "480", "0.25"),
              "JSS", "199405563K", "1994-05-16", "First Citizens Bank, Jurong East Branch", "362-308-571-649",
              ("ONG KIM HOCK", "S5309827H")),
    _supplier("Senoko Machine Tools Pte. Ltd.", ("29 Senoko Loop, #02-08", "Singapore 758188"), "Factory Machine",
              ("CNC plasma cutting table", "Installation and training"), 1, _pack("CRATES", 1, "2600", "11"),
              "SMT", "201005519B", "2010-05-03", "Harbourfront Merchant Bank, Woodlands Branch", "363-641-287-905",
              ("LEE HWEE MIN", "S7309452D")),
    _supplier("Tuas Lifting Equipment Pte. Ltd.", ("4 Tuas South Street 3", "Singapore 638043"), "Overhead Crane",
              ("Overhead crane, 5-tonne", "Installation and load test"), 2, _pack("UNITS", 1, "3200", "9"),
              "TLE", "201211583A", "2012-08-20", "Straits Commercial Bank, Tuas Branch", "364-092-735-814",
              ("KUMAR SELVAM", "S7104693G")),
    _customer("Melaka Struktur Sdn. Bhd.", ("No. 12, Jalan PKS 3, Kawasan Perindustrian Krubong", "75260 Melaka, Malaysia"),
              ("Fabricated steel column, 8 m", "Shop drawings and inspection"), 40, _pack("UNITS", 1, "900", "2.2"),
              "MSB", "Port Klang"),
    _supplier("Kian Teck Coatings Pte. Ltd.", ("33 Kian Teck Road", "Singapore 628784"), "Paint Supplies",
              ("Zinc-rich primer, 20 L pail", "Delivery"), 1200, _pack("PALLETS", 24, "28", "0.025"),
              "KTC", "200807246C", "2008-07-14", "First Citizens Bank, Boon Lay Branch", "365-517-028-463",
              ("YAP SOOK CHING", "S6911804E")),
)

# KESTREL AUTOMATION PTE. LTD.: builds conveyor and sortation lines
KESTREL = (
    _supplier("Bukit Batok Drives Pte. Ltd.", ("21 Bukit Batok Crescent, #06-71", "Singapore 658065"), "Motor Supplies",
              ("Geared motor 2.2 kW", "Freight and handling"), 300, _pack("CRATES", 10, "28", "0.03"),
              "BBD", "201214471K", "2012-09-10", "First Citizens Bank, Bukit Batok Branch", "411-283-905-176",
              ("TAN SOON HUAT", "S6713390A")),
    _customer("Pelabuhan Logistik Sdn. Bhd.", ("Lot 7, Jalan Kem, Westport Distripark", "42009 Pelabuhan Klang, Selangor, Malaysia"),
              ("Parcel sortation line PS-40", "Installation supervision and commissioning"), 6,
              _pack("CRATES", 1, "3800", "22"), "PLH", "Port Klang"),
    _supplier("Tuas Belting Industries Pte. Ltd.", ("7 Tuas Link 2", "Singapore 638553"), "Conveyor Belting",
              ("Rubber conveyor belt, 800 mm, 100 m roll", "Splicing and delivery"), 90, _pack("UNITS", 1, "620", "1.4"),
              "TBI", "200610082D", "2006-05-22", "Straits Commercial Bank, Tuas Branch", "412-907-334-528",
              ("CHAN KOK MENG", "S6208815C")),
    _supplier("Ang Mo Kio Laser Cutting Pte. Ltd.", ("5 Ang Mo Kio Industrial Park 2A, #03-12", "Singapore 567760"),
              "Factory Machine", ("Fibre laser cutting machine 3 kW", "Installation and training"), 1,
              _pack("CRATES", 1, "5200", "16"), "AMK", "201511906E", "2015-08-03",
              "Harbourfront Merchant Bank, Ang Mo Kio Branch", "413-662-019-847", ("LIM WEI CHERNG", "S7809921D")),
    _supplier("Gul Way Hoists Pte. Ltd.", ("16 Gul Way", "Singapore 629199"), "Material Handling",
              ("Electric pallet stacker, 1.5-tonne", "Delivery and commissioning"), 5, _pack("UNITS", 1, "1150", "3.6"),
              "GWH", "200905547F", "2009-04-27", "Straits Commercial Bank, Boon Lay Branch", "414-031-578-260",
              ("MUTHU KRISHNAN", "S6604418F")),
    _customer("Kulim Hi-Tech Packaging Sdn. Bhd.", ("Lot 18, Jalan Hi-Tech 4, Kulim Hi-Tech Park",
                                                   "09090 Kulim, Kedah, Malaysia"),
              ("Carton conveyor line CC-25", "Installation supervision and commissioning"), 8,
              _pack("CRATES", 1, "2900", "17"), "KHP", "Penang"),
    _supplier("Kaki Bukit Electrical Pte. Ltd.", ("62 Kaki Bukit Avenue 1, #04-31", "Singapore 417943"), "Control Panels",
              ("PLC control panel, conveyor duty", "Wiring and testing"), 40, _pack("CRATES", 1, "180", "0.9"),
              "KBE", "201309258G", "2013-03-18", "First Citizens Bank, Kaki Bukit Branch", "415-548-203-697",
              ("YEO HAN CHUAN", "S7402276H")),
)

# LUMEN AQUA SYSTEMS PTE. LTD.: builds water filtration units. Its biggest supplier,
# Harbour Ridge Filtration, is run by Lumen's own chief executive (the related_party case)
LUMEN_CEO = ("NG LI HUA", "S8077213Q")
LUMEN = (
    _supplier("Changi Membrane Technologies Pte. Ltd.", ("12 Changi North Way, #02-08", "Singapore 498810"),
              "Membrane Supplies", ("Reverse-osmosis membrane element, 8 in", "Freight and handling"), 400,
              _pack("CARTONS", 4, "16", "0.06"), "CMT", "201108815J", "2011-02-14",
              "Harbourfront Merchant Bank, Changi Branch", "421-306-781-559", ("SEAH BOON KIAT", "S6911453J")),
    _customer("Air Murni Engineering Sdn. Bhd.", ("No. 9, Jalan Sungai Puloh, Kawasan Perindustrian Sungai Puloh",
                                                 "42100 Klang, Selangor, Malaysia"),
              ("Containerised water treatment unit WT-50", "Commissioning and operator training"), 10,
              _pack("UNITS", 1, "7400", "33"), "AMR", "Port Klang"),
    _supplier("Gul Circle Stainless Pte. Ltd.", ("41 Gul Circle", "Singapore 629576"), "Stainless Pipes",
              ("Stainless steel pipe 316L, 6 m", "Cutting and delivery"), 700, _pack("BUNDLES", 10, "42", "0.04"),
              "GCS", "200703369L", "2007-03-05", "Straits Commercial Bank, Jurong Branch", "422-519-064-382",
              ("LOH CHENG HOCK", "S6307728L")),
    _supplier("Tampines Moulding Systems Pte. Ltd.", ("8 Tampines Industrial Avenue 5, #05-11", "Singapore 528762"),
              "Factory Machine", ("Rotational moulding machine", "Installation and training"), 1,
              _pack("CRATES", 1, "4300", "14"), "TMS", "201612240M", "2016-06-13",
              "First Citizens Bank, Tampines Branch", "423-847-219-603", ("JOSEPH FERNANDEZ", "S7510086M")),
    _supplier("Paya Lebar Pumps Pte. Ltd.", ("10 Paya Lebar Crescent, #01-07", "Singapore 536006"), "Pumps",
              ("High-pressure pump, 15 kW", "Delivery and alignment"), 24, _pack("CRATES", 1, "240", "0.7"),
              "PLP", "200208173N", "2002-08-19", "Harbourfront Merchant Bank, Paya Lebar Branch", "424-150-936-718",
              ("KOH AH BENG", "S5808894N")),
    _customer("Johor Aqua Utilities Sdn. Bhd.", ("PLO 88, Jalan Tanjung 3, Kawasan Perindustrian Tanjung Langsat",
                                                "81700 Pasir Gudang, Johor, Malaysia"),
              ("Containerised water treatment unit WT-80", "Commissioning and operator training"), 6,
              _pack("UNITS", 1, "9800", "42"), "JAU", "Pasir Gudang"),
    _supplier("Harbour Ridge Filtration Pte. Ltd.", ("3 Pandan Crescent, #03-05", "Singapore 128461"),
              "Filtration Components", ("Multimedia filter vessel, 1.2 m", "Delivery and installation"), 80,
              _pack("UNITS", 1, "520", "2.4"), "HRF", "202104558R", "2021-04-26",
              "Straits Commercial Bank, Clementi Branch", "425-692-310-844", LUMEN_CEO),
)

# NORTHSTAR COLD CHAIN PTE. LTD.: builds cold rooms and blast freezers. Its payment for a
# machine comes back from the same supplier as a "customer receipt" (the round_trip case)
NORTHSTAR = (
    _supplier("Senoko Insulation Pte. Ltd.", ("15 Senoko Way, #02-03", "Singapore 758052"), "Insulation Supplies",
              ("Polyurethane insulated panel, 100 mm", "Delivery"), 1800, _pack("PALLETS", 20, "24", "0.36"),
              "SNI", "201007716P", "2010-07-26", "First Citizens Bank, Sembawang Branch", "431-275-608-913",
              ("TEO BOON HWA", "S6610257P")),
    _customer("Klang Frozen Foods Sdn. Bhd.", ("Lot 33, Jalan Sungai Rasa, Kawasan Perindustrian Bukit Raja",
                                              "41050 Klang, Selangor, Malaysia"),
              ("Walk-in cold room CR-60", "Installation supervision and commissioning"), 14,
              _pack("CRATES", 1, "2400", "19"), "KFF", "Port Klang"),
    _supplier("Tuas Aluminium Pte. Ltd.", ("20 Tuas South Avenue 6", "Singapore 637021"), "Aluminium Sheets",
              ("Aluminium chequer plate, 3 mm", "Cutting and delivery"), 600, _pack("BUNDLES", 20, "21", "0.02"),
              "TAL", "199811402R", "1998-11-09", "Straits Commercial Bank, Tuas Branch", "432-508-917-246",
              ("NG KOK PENG", "S5912360R")),
    _supplier("Kranji Refrigeration Pte. Ltd.", ("9 Kranji Crescent, #01-02", "Singapore 728662"),
              "Factory Machine", ("Panel foaming press", "Installation and training"), 1,
              _pack("CRATES", 1, "3900", "13"), "KRE", "201418827S", "2014-10-06",
              "Harbourfront Merchant Bank, Kranji Branch", "433-761-024-589", ("RAVI CHANDRAN", "S7205581S")),
    _supplier("Benoi Material Handling Pte. Ltd.", ("27 Benoi Road", "Singapore 629911"), "Reach Trucks",
              ("Electric reach truck, 2-tonne", "Delivery and registration"), 3, _pack("UNITS", 1, "3300", "8.4"),
              "BMH", "200511954T", "2005-11-21", "First Citizens Bank, Jurong West Branch", "434-019-372-805",
              ("LEE CHOON TECK", "S6409915T")),
    _customer("Kuantan Seafood Processing Sdn. Bhd.", ("Lot 12, Jalan Industri Semambu 6, Kawasan Perindustrian Semambu",
                                                      "25350 Kuantan, Pahang, Malaysia"),
              ("Blast freezer BF-20", "Installation supervision and commissioning"), 9,
              _pack("CRATES", 1, "3100", "21"), "KSP", "Kuantan"),
    _supplier("Woodlands Compressors Pte. Ltd.", ("30 Woodlands Industrial Park E1, #02-10", "Singapore 757709"),
              "Refrigeration Compressors", ("Semi-hermetic compressor, 30 kW", "Delivery and commissioning"), 30,
              _pack("CRATES", 1, "410", "0.8"), "WCP", "201320633U", "2013-01-28",
              "Harbourfront Merchant Bank, Woodlands Branch", "435-246-890-137", ("GOH SIEW LING", "S7311026U")),
)
