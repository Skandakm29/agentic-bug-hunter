import csv
from orchestrator import process_code

INPUT_FILE = "samples.csv"
OUTPUT_FILE = "output.csv"

def generate_output():
    with open(INPUT_FILE, newline='', encoding="utf-8") as infile, \
         open(OUTPUT_FILE, mode='w', newline='', encoding="utf-8") as outfile:

        reader = csv.DictReader(infile)

        fieldnames = ["ID", "Predicted Explanation", "Predicted Correct Code"]
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)

        writer.writeheader()

        for row in reader:
            code_id = row.get("ID")
            code = row.get("Code", "")

            result = process_code(code)

            writer.writerow({
                "ID": code_id,
                "Predicted Explanation": result["explanation"],
                "Predicted Correct Code": result["corrected_code"]
            })

    print("Output CSV generated successfully.")

if __name__ == "__main__":
    generate_output()