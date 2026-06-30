import json

OUTPUT_JSON = "cma_to_csd_mapping.json"

# Authoritative Census Metropolitan Area (CMA) to Census Subdivision (CSD) relationships
CMA_DATA = [
    {
        "cma_code": "537",
        "cma_name": "Hamilton",
        "type": "Census metropolitan area",
        "census_subdivisions": [
            {"csd_code": "3525005", "csd_name": "Burlington", "csd_type": "City"},
            {"csd_code": "3525030", "csd_name": "Guelph/Eramosa", "csd_type": "Township"},
            {"csd_code": "3525001", "csd_name": "Hamilton", "csd_type": "City"},
            {"csd_code": "3524009", "csd_name": "Grimsby", "csd_type": "Town"}
        ]
    },
    {
        "cma_code": "535",
        "cma_name": "Toronto",
        "type": "Census metropolitan area",
        "census_subdivisions": [
            {"csd_code": "3520005", "csd_name": "Toronto", "csd_type": "City"},
            {"csd_code": "3519036", "csd_name": "Markham", "csd_type": "City"},
            {"csd_code": "3519046", "csd_name": "Richmond Hill", "csd_type": "Town"},
            {"csd_code": "3519028", "csd_name": "Vaughan", "csd_type": "City"},
            {"csd_code": "3521010", "csd_name": "Mississauga", "csd_type": "City"},
            {"csd_code": "3521024", "csd_name": "Brampton", "csd_type": "City"},
            {"csd_code": "3519011", "csd_name": "Oakville", "csd_type": "Town"},
            {"csd_code": "3518005", "csd_name": "Pickering", "csd_type": "City"},
            {"csd_code": "3518009", "csd_name": "Ajax", "csd_type": "Town"},
            {"csd_code": "3518013", "csd_name": "Whitby", "csd_type": "Town"},
            {"csd_code": "3518017", "csd_name": "Oshawa", "csd_type": "City"}
        ]
    },
    {
        "cma_code": "462",
        "cma_name": "Montréal",
        "type": "Census metropolitan area",
        "census_subdivisions": [
            {"csd_code": "2466023", "csd_name": "Montréal", "csd_type": "Ville"},
            {"csd_code": "2465005", "csd_name": "Laval", "csd_type": "Ville"},
            {"csd_code": "2458007", "csd_name": "Longueuil", "csd_type": "Ville"},
            {"csd_code": "2466142", "csd_name": "Dollard-des-Ormeaux", "csd_type": "Ville"},
            {"csd_code": "2473015", "csd_name": "Terrebonne", "csd_type": "Ville"},
            {"csd_code": "2472015", "csd_name": "Repentigny", "csd_type": "Ville"}
        ]
    },
    {
        "cma_code": "933",
        "cma_name": "Vancouver",
        "type": "Census metropolitan area",
        "census_subdivisions": [
            {"csd_code": "5915022", "csd_name": "Vancouver", "csd_type": "City"},
            {"csd_code": "5915004", "csd_name": "Surrey", "csd_type": "City"},
            {"csd_code": "5915015", "csd_name": "Burnaby", "csd_type": "City"},
            {"csd_code": "5915011", "csd_name": "Richmond", "csd_type": "City"},
            {"csd_code": "5915055", "csd_name": "Coquitlam", "csd_type": "City"},
            {"csd_code": "5915046", "csd_name": "Langley", "csd_type": "District municipality"}
        ]
    },
    {
        "cma_code": "825",
        "cma_name": "Calgary",
        "type": "Census metropolitan area",
        "census_subdivisions": [
            {"csd_code": "4806016", "csd_name": "Calgary", "csd_type": "City"},
            {"csd_code": "4806021", "csd_name": "Airdrie", "csd_type": "City"},
            {"csd_code": "4806002", "csd_name": "Cochrane", "csd_type": "Town"},
            {"csd_code": "4806004", "csd_name": "Chestermere", "csd_type": "City"}
        ]
    },
    {
        "cma_code": "835",
        "cma_name": "Edmonton",
        "type": "Census metropolitan area",
        "census_subdivisions": [
            {"csd_code": "4811061", "csd_name": "Edmonton", "csd_type": "City"},
            {"csd_code": "4811062", "csd_name": "St. Albert", "csd_type": "City"},
            {"csd_code": "4811056", "csd_name": "Sherwood Park", "csd_type": "Urban service area"},
            {"csd_code": "4811016", "csd_name": "Leduc", "csd_type": "City"},
            {"csd_code": "4811034", "csd_name": "Spruce Grove", "csd_type": "City"}
        ]
    },
    {
        "cma_code": "505",
        "cma_name": "Ottawa - Gatineau",
        "type": "Census metropolitan area",
        "census_subdivisions": [
            {"csd_code": "3506008", "csd_name": "Ottawa", "csd_type": "City"},
            {"csd_code": "2481017", "csd_name": "Gatineau", "csd_type": "Ville"}
        ]
    }
]

def generate_json():
    print(f"Generating hierarchical map file...")
    
    # Sort subdivisions alphabetically inside each area configuration block
    for cma in CMA_DATA:
        cma["census_subdivisions"].sort(key=lambda x: x["csd_name"])
        
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(CMA_DATA, f, indent=4, ensure_ascii=False)
        
    print(f"Success! '{OUTPUT_JSON}' has been compiled successfully locally.")

if __name__ == "__main__":
    generate_json()