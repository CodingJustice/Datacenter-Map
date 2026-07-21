import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.OptionalDouble;
import java.util.Set;

/**
 * Builds map-data.js for the U.S. data-center, power, nuclear, and water map.
 *
 * Usage:
 *   javac MapGenerator.java
 *   java MapGenerator
 *
 * Optional arguments:
 *   --data-centers path/to/data_centers.csv
 *   --power-plants path/to/power_plants.csv
 *   --nuclear-plants path/to/nuclear_plants.csv
 *   --water-sources path/to/water_sources.csv
 *   --output-js path/to/map-data.js
 *   --generated-at "July 21, 2026 12:00 PM"
 *
 * This generator uses only the Java standard library. It reads CSV exports,
 * removes old market-hub placeholders, recomputes proximity fields, and writes
 * a cacheable JavaScript data file that works with the split HTML/CSS/JS page.
 */
public final class MapGenerator {
    private static final int MAX_RECORDS = 60_000;
    private static final int MAX_FIELD_LENGTH = 12_000;

    private static final Path DEFAULT_DATA_CENTERS =
        Path.of("us_ai_datacenters_nuclear_map_data_centers.csv");
    private static final Path DEFAULT_POWER_PLANTS =
        Path.of("us_ai_datacenters_nuclear_map_power_plants.csv");
    private static final Path DEFAULT_NUCLEAR_PLANTS =
        Path.of("us_ai_datacenters_nuclear_map_nuclear_plants.csv");
    private static final Path DEFAULT_WATER_SOURCES =
        Path.of("us_ai_datacenters_nuclear_map_water_sources.csv");
    private static final Path DEFAULT_OUTPUT_JS = Path.of("map-data.js");

    private static final List<String> DATA_CENTER_FIELDS = List.of(
        "id",
        "name",
        "developer",
        "status",
        "status_group",
        "category",
        "facility_type",
        "ai_classification",
        "confidence",
        "capacity_mw",
        "operational_capacity_mw",
        "planned_capacity_mw",
        "capacity_label",
        "landscape_weight",
        "location",
        "city",
        "county",
        "state",
        "latitude",
        "longitude",
        "precision",
        "powered_by",
        "energy_source",
        "utility",
        "onsite_generation_mw",
        "water_cooling_type",
        "water_reported_mgd",
        "water_notes",
        "community_status",
        "investment_usd",
        "land_acres",
        "jobs_construction",
        "jobs_permanent",
        "nearest_nuclear_name",
        "nearest_nuclear_state",
        "nearest_nuclear_distance_mi",
        "nearest_nuclear_capacity_mw",
        "nearest_nuclear_latitude",
        "nearest_nuclear_longitude",
        "nearest_power_name",
        "nearest_power_state",
        "nearest_power_distance_mi",
        "nearest_power_capacity_mw",
        "nearest_power_source",
        "nearest_power_latitude",
        "nearest_power_longitude",
        "nearest_water_name",
        "nearest_water_type",
        "nearest_water_distance_mi",
        "nearest_water_latitude",
        "nearest_water_longitude",
        "source",
        "source_url",
        "source_count",
        "notes"
    );

    private static final List<String> POWER_FIELDS = List.of(
        "plant_id",
        "name",
        "operator",
        "state",
        "county",
        "latitude",
        "longitude",
        "capacity_mw",
        "status_group",
        "statuses",
        "technologies",
        "energy_sources",
        "type",
        "is_nuclear",
        "source",
        "source_url",
        "notes"
    );

    private static final List<String> NUCLEAR_FIELDS = POWER_FIELDS;

    private static final List<String> WATER_FIELDS = List.of(
        "name",
        "type",
        "state",
        "latitude",
        "longitude",
        "source",
        "source_url",
        "notes"
    );

    private static final Set<String> NUMERIC_FIELDS = new LinkedHashSet<>(List.of(
        "capacity_mw",
        "operational_capacity_mw",
        "planned_capacity_mw",
        "landscape_weight",
        "latitude",
        "longitude",
        "onsite_generation_mw",
        "water_reported_mgd",
        "investment_usd",
        "land_acres",
        "jobs_construction",
        "jobs_permanent",
        "nearest_nuclear_distance_mi",
        "nearest_nuclear_capacity_mw",
        "nearest_nuclear_latitude",
        "nearest_nuclear_longitude",
        "nearest_power_distance_mi",
        "nearest_power_capacity_mw",
        "nearest_power_latitude",
        "nearest_power_longitude",
        "nearest_water_distance_mi",
        "nearest_water_latitude",
        "nearest_water_longitude",
        "source_count"
    ));

    private static final Set<String> BOOLEAN_FIELDS = Set.of("is_nuclear");

    private MapGenerator() {
    }

    public static void main(String[] args) throws IOException {
        Config config = Config.parse(args);

        List<Map<String, String>> dataCenters = readCsv(config.dataCentersCsv);
        List<Map<String, String>> powerPlants = readCsv(config.powerPlantsCsv);
        List<Map<String, String>> nuclearPlants = readCsv(config.nuclearPlantsCsv);
        List<Map<String, String>> waterSources = readCsv(config.waterSourcesCsv);

        dataCenters.removeIf(MapGenerator::isMarketHubRecord);

        validateRequiredFields(dataCenters, "data center", "name", "latitude", "longitude");
        validateRequiredFields(powerPlants, "power plant", "name", "latitude", "longitude");
        validateRequiredFields(nuclearPlants, "nuclear plant", "name", "latitude", "longitude");
        validateRequiredFields(waterSources, "water source", "name", "latitude", "longitude");

        normalizeDataCenters(dataCenters);
        normalizePowerPlants(powerPlants);
        normalizePowerPlants(nuclearPlants);
        normalizeWaterSources(waterSources);
        enrichDataCenterProximity(dataCenters, nuclearPlants, powerPlants, waterSources);

        writeMapData(config.outputJs, dataCenters, nuclearPlants, powerPlants, waterSources, config.generatedAt);

        System.out.printf(Locale.US,
            "Wrote %s with %,d data centers, %,d nuclear records, %,d power records, and %,d water records.%n",
            config.outputJs.toAbsolutePath().normalize(),
            dataCenters.size(),
            nuclearPlants.size(),
            powerPlants.size(),
            waterSources.size());
    }

    private static List<Map<String, String>> readCsv(Path path) throws IOException {
        String text = Files.readString(path, StandardCharsets.UTF_8);
        List<List<String>> rows = parseCsv(text);
        if (rows.isEmpty()) {
            throw new IOException("CSV has no header row: " + path);
        }

        List<String> header = rows.get(0);
        Set<String> seenHeaders = new LinkedHashSet<>();
        for (String column : header) {
            if (!seenHeaders.add(column)) {
                throw new IOException("Duplicate CSV header " + column + " in " + path);
            }
        }

        List<Map<String, String>> records = new ArrayList<>();
        for (int index = 1; index < rows.size(); index += 1) {
            List<String> row = rows.get(index);
            if (row.stream().allMatch(String::isBlank)) {
                continue;
            }
            if (records.size() >= MAX_RECORDS) {
                throw new IOException("CSV exceeds " + MAX_RECORDS + " records: " + path);
            }

            Map<String, String> record = new LinkedHashMap<>();
            for (int column = 0; column < header.size(); column += 1) {
                String value = column < row.size() ? row.get(column) : "";
                record.put(header.get(column), cleanText(value));
            }
            records.add(record);
        }

        return records;
    }

    private static List<List<String>> parseCsv(String text) throws IOException {
        List<List<String>> rows = new ArrayList<>();
        List<String> row = new ArrayList<>();
        StringBuilder field = new StringBuilder();
        boolean inQuotes = false;

        for (int index = 0; index < text.length(); index += 1) {
            char character = text.charAt(index);

            if (inQuotes) {
                if (character == '"') {
                    if (index + 1 < text.length() && text.charAt(index + 1) == '"') {
                        field.append('"');
                        index += 1;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    appendFieldCharacter(field, character);
                }
                continue;
            }

            if (character == '"') {
                inQuotes = true;
            } else if (character == ',') {
                row.add(field.toString());
                field.setLength(0);
            } else if (character == '\r' || character == '\n') {
                row.add(field.toString());
                rows.add(row);
                row = new ArrayList<>();
                field.setLength(0);
                if (character == '\r' && index + 1 < text.length() && text.charAt(index + 1) == '\n') {
                    index += 1;
                }
            } else {
                appendFieldCharacter(field, character);
            }
        }

        if (inQuotes) {
            throw new IOException("CSV contains an unterminated quoted field.");
        }

        if (field.length() > 0 || !row.isEmpty()) {
            row.add(field.toString());
            rows.add(row);
        }

        return rows;
    }

    private static void appendFieldCharacter(StringBuilder field, char character) throws IOException {
        if (field.length() >= MAX_FIELD_LENGTH) {
            throw new IOException("CSV field exceeds " + MAX_FIELD_LENGTH + " characters.");
        }
        field.append(character);
    }

    private static void validateRequiredFields(
        List<Map<String, String>> records,
        String recordType,
        String... fields
    ) {
        for (Map<String, String> record : records) {
            for (String field : fields) {
                if (!record.containsKey(field) || record.get(field).isBlank()) {
                    throw new IllegalArgumentException("Missing " + field + " for " + recordType + " record.");
                }
            }
        }
    }

    private static boolean isMarketHubRecord(Map<String, String> record) {
        String statusGroup = record.getOrDefault("status_group", "").trim().toLowerCase(Locale.ROOT);
        String category = record.getOrDefault("category", "").trim().toLowerCase(Locale.ROOT);
        String facilityType = record.getOrDefault("facility_type", "").trim().toLowerCase(Locale.ROOT);
        return statusGroup.equals("market hub")
            || category.equals("market hub")
            || facilityType.equals("market_hub")
            || facilityType.equals("market hub");
    }

    private static void normalizeDataCenters(List<Map<String, String>> records) {
        for (Map<String, String> record : records) {
            ensureFields(record, DATA_CENTER_FIELDS);
            if (record.get("status_group").isBlank()) {
                record.put("status_group", normalizeStatusGroup(record.get("status")));
            } else {
                record.put("status_group", normalizeStatusGroup(record.get("status_group")));
            }
            if (record.get("capacity_label").isBlank()) {
                record.put("capacity_label", formatMegawatts(record.get("capacity_mw")));
            }
            record.put("source_url", sanitizeUrl(record.get("source_url")));
        }
    }

    private static void normalizePowerPlants(List<Map<String, String>> records) {
        for (Map<String, String> record : records) {
            ensureFields(record, POWER_FIELDS);
            if (record.get("status_group").isBlank()) {
                record.put("status_group", normalizeStatusGroup(record.get("statuses")));
            } else {
                record.put("status_group", normalizeStatusGroup(record.get("status_group")));
            }
            if (record.get("type").isBlank()) {
                record.put("type", isNuclearRecord(record) ? "Nuclear power plant" : "Power plant");
            }
            if (record.get("is_nuclear").isBlank()) {
                record.put("is_nuclear", Boolean.toString(isNuclearRecord(record)));
            }
            if (record.get("source").isBlank()) {
                record.put("source", "EIA-860M / Compute Atlas");
            }
            record.put("source_url", sanitizeUrl(record.get("source_url")));
        }
    }

    private static void normalizeWaterSources(List<Map<String, String>> records) {
        for (Map<String, String> record : records) {
            ensureFields(record, WATER_FIELDS);
            if (record.get("source").isBlank()) {
                record.put("source", "Natural Earth / USGS context");
            }
            record.put("source_url", sanitizeUrl(record.get("source_url")));
        }
    }

    private static void ensureFields(Map<String, String> record, List<String> fields) {
        for (String field : fields) {
            record.putIfAbsent(field, "");
        }
    }

    private static boolean isNuclearRecord(Map<String, String> record) {
        String combined = (
            record.getOrDefault("type", "") + " "
            + record.getOrDefault("technologies", "") + " "
            + record.getOrDefault("energy_sources", "")
        ).toLowerCase(Locale.ROOT);
        return combined.contains("nuclear") || combined.contains("nuc");
    }

    private static String normalizeStatusGroup(String status) {
        String combined = cleanText(status).toLowerCase(Locale.ROOT).replace('_', ' ');
        if (combined.contains("under construction") || combined.contains("construction")
            || combined.contains("(v)") || combined.contains("(ts)")) {
            return "Under construction";
        }
        if (combined.contains("permitted")) {
            return "Permitted";
        }
        if (combined.contains("proposed")) {
            return "Proposed";
        }
        if (combined.contains("cancelled") || combined.contains("canceled")) {
            return "Cancelled";
        }
        if (combined.contains("planned")) {
            return "Planned";
        }
        if (combined.contains("operating") || combined.contains("operational")
            || combined.contains("(op)") || combined.contains("standby")) {
            return "Operating";
        }
        return "Other";
    }

    private static String formatMegawatts(String value) {
        OptionalDouble number = parseDouble(value);
        if (number.isEmpty()) {
            return "";
        }
        return String.format(Locale.US, "%,.0f MW", number.getAsDouble());
    }

    private static void enrichDataCenterProximity(
        List<Map<String, String>> dataCenters,
        List<Map<String, String>> nuclearPlants,
        List<Map<String, String>> powerPlants,
        List<Map<String, String>> waterSources
    ) {
        for (Map<String, String> site : dataCenters) {
            NearestResult nearestNuclear = nearestRecord(site, nuclearPlants);
            if (nearestNuclear != null) {
                applyNearest(site, nearestNuclear, "nuclear");
            }

            NearestResult nearestPower = nearestRecord(site, powerPlants);
            if (nearestPower != null) {
                applyNearest(site, nearestPower, "power");
            }

            NearestResult nearestWater = nearestRecord(site, waterSources);
            if (nearestWater != null) {
                applyNearest(site, nearestWater, "water");
            }
        }
    }

    private static NearestResult nearestRecord(
        Map<String, String> site,
        List<Map<String, String>> records
    ) {
        OptionalDouble siteLat = parseDouble(site.get("latitude"));
        OptionalDouble siteLon = parseDouble(site.get("longitude"));
        if (siteLat.isEmpty() || siteLon.isEmpty() || records.isEmpty()) {
            return null;
        }

        Map<String, String> nearest = null;
        double nearestDistance = Double.POSITIVE_INFINITY;
        for (Map<String, String> record : records) {
            OptionalDouble recordLat = parseDouble(record.get("latitude"));
            OptionalDouble recordLon = parseDouble(record.get("longitude"));
            if (recordLat.isEmpty() || recordLon.isEmpty()) {
                continue;
            }

            double distance = haversineMiles(
                siteLat.getAsDouble(),
                siteLon.getAsDouble(),
                recordLat.getAsDouble(),
                recordLon.getAsDouble()
            );
            if (distance < nearestDistance) {
                nearestDistance = distance;
                nearest = record;
            }
        }

        return nearest == null ? null : new NearestResult(nearest, nearestDistance);
    }

    private static void applyNearest(Map<String, String> site, NearestResult nearest, String kind) {
        Map<String, String> record = nearest.record;
        site.put("nearest_" + kind + "_name", record.getOrDefault("name", ""));
        site.put("nearest_" + kind + "_distance_mi", String.format(Locale.US, "%.1f", nearest.distanceMiles));
        site.put("nearest_" + kind + "_latitude", record.getOrDefault("latitude", ""));
        site.put("nearest_" + kind + "_longitude", record.getOrDefault("longitude", ""));

        if (kind.equals("nuclear")) {
            site.put("nearest_nuclear_state", record.getOrDefault("state", ""));
            site.put("nearest_nuclear_capacity_mw", record.getOrDefault("capacity_mw", ""));
        } else if (kind.equals("power")) {
            site.put("nearest_power_state", record.getOrDefault("state", ""));
            site.put("nearest_power_capacity_mw", record.getOrDefault("capacity_mw", ""));
            site.put("nearest_power_source", record.getOrDefault("energy_sources", ""));
        } else if (kind.equals("water")) {
            site.put("nearest_water_type", record.getOrDefault("type", ""));
        }
    }

    private static double haversineMiles(double lat1, double lon1, double lat2, double lon2) {
        double radiusMiles = 3958.7613;
        double phi1 = Math.toRadians(lat1);
        double phi2 = Math.toRadians(lat2);
        double deltaPhi = Math.toRadians(lat2 - lat1);
        double deltaLambda = Math.toRadians(lon2 - lon1);
        double a = Math.sin(deltaPhi / 2) * Math.sin(deltaPhi / 2)
            + Math.cos(phi1) * Math.cos(phi2)
            * Math.sin(deltaLambda / 2) * Math.sin(deltaLambda / 2);
        return radiusMiles * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    private static void writeMapData(
        Path output,
        List<Map<String, String>> dataCenters,
        List<Map<String, String>> nuclearPlants,
        List<Map<String, String>> powerPlants,
        List<Map<String, String>> waterSources,
        String generatedAt
    ) throws IOException {
        StringBuilder js = new StringBuilder();
        js.append("(function () {\r\n");
        js.append("  \"use strict\";\r\n\r\n");
        js.append("  window.DatacenterMapData = Object.freeze({\r\n");
        js.append("    generatedAt: ").append(jsonString(generatedAt)).append(",\r\n");
        js.append("    sources: Object.freeze(");
        appendRecords(js, sourceMetadata(), List.of("name", "url", "description"));
        js.append("),\r\n");
        js.append("    dataCenters: Object.freeze(");
        appendRecords(js, dataCenters, DATA_CENTER_FIELDS);
        js.append("),\r\n");
        js.append("    nuclearPlants: Object.freeze(");
        appendRecords(js, nuclearPlants, NUCLEAR_FIELDS);
        js.append("),\r\n");
        js.append("    powerPlants: Object.freeze(");
        appendRecords(js, powerPlants, POWER_FIELDS);
        js.append("),\r\n");
        js.append("    waterSources: Object.freeze(");
        appendRecords(js, waterSources, WATER_FIELDS);
        js.append(")\r\n");
        js.append("  });\r\n");
        js.append("}());\r\n");

        Files.writeString(output, js.toString(), StandardCharsets.UTF_8);
    }

    private static List<Map<String, String>> sourceMetadata() {
        List<Map<String, String>> sources = new ArrayList<>();
        sources.add(sourceRecord(
            "Compute Atlas",
            "https://www.compute-atlas.com/",
            "Open, source-cited U.S. data-center facility dataset (CC BY 4.0)."));
        sources.add(sourceRecord(
            "EIA-860M",
            "https://www.eia.gov/electricity/data/eia860m/index.php",
            "Official monthly U.S. generator inventory for operating and planned power plants."));
        sources.add(sourceRecord(
            "Water source reference points",
            "https://www.usgs.gov/national-hydrography/national-hydrography-dataset",
            "Named surface-water context points for proximity calculations."));
        return sources;
    }

    private static Map<String, String> sourceRecord(String name, String url, String description) {
        Map<String, String> record = new LinkedHashMap<>();
        record.put("name", name);
        record.put("url", url);
        record.put("description", description);
        return record;
    }

    private static void appendRecords(
        StringBuilder js,
        List<Map<String, String>> records,
        List<String> fields
    ) {
        js.append("[");
        for (int rowIndex = 0; rowIndex < records.size(); rowIndex += 1) {
            if (rowIndex > 0) {
                js.append(",");
            }
            appendRecord(js, records.get(rowIndex), fields);
        }
        js.append("]");
    }

    private static void appendRecord(StringBuilder js, Map<String, String> record, List<String> fields) {
        js.append("{");
        boolean wroteField = false;
        for (String field : fields) {
            String value = record.getOrDefault(field, "");
            boolean requiredCoordinate = field.equals("latitude") || field.equals("longitude");
            if (value.isBlank() && !requiredCoordinate) {
                continue;
            }

            String jsonValue = jsonValue(field, value, requiredCoordinate);
            if (jsonValue.isBlank()) {
                continue;
            }

            if (wroteField) {
                js.append(",");
            }
            wroteField = true;
            js.append(jsonString(field)).append(":").append(jsonValue);
        }
        js.append("}");
    }

    private static String jsonValue(String field, String value, boolean requiredCoordinate) {
        if (NUMERIC_FIELDS.contains(field)) {
            OptionalDouble number = parseDouble(value);
            if (number.isPresent()) {
                return Double.toString(number.getAsDouble());
            }
            return requiredCoordinate ? "null" : "";
        }

        if (BOOLEAN_FIELDS.contains(field)) {
            return Boolean.toString(parseBoolean(value));
        }

        return jsonString(cleanText(value));
    }

    private static OptionalDouble parseDouble(String value) {
        if (value == null) {
            return OptionalDouble.empty();
        }
        String text = value.trim().replace(",", "");
        if (text.isEmpty() || text.equalsIgnoreCase("na") || text.equalsIgnoreCase("n/a")) {
            return OptionalDouble.empty();
        }
        try {
            double number = Double.parseDouble(text);
            return Double.isFinite(number) ? OptionalDouble.of(number) : OptionalDouble.empty();
        } catch (NumberFormatException ignored) {
            return OptionalDouble.empty();
        }
    }

    private static boolean parseBoolean(String value) {
        String text = cleanText(value).toLowerCase(Locale.ROOT);
        return text.equals("true") || text.equals("1") || text.equals("yes");
    }

    private static String cleanText(String value) {
        if (value == null) {
            return "";
        }
        StringBuilder cleaned = new StringBuilder();
        value.codePoints()
            .filter((codePoint) -> codePoint >= 0x20 || codePoint == '\t')
            .limit(MAX_FIELD_LENGTH)
            .forEach(cleaned::appendCodePoint);
        return cleaned.toString().trim();
    }

    private static String sanitizeUrl(String value) {
        String text = cleanText(value);
        String lower = text.toLowerCase(Locale.ROOT);
        return lower.startsWith("https://") || lower.startsWith("http://") ? text : "";
    }

    private static String jsonString(String value) {
        StringBuilder escaped = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index += 1) {
            char character = value.charAt(index);
            switch (character) {
                case '"':
                    escaped.append("\\\"");
                    break;
                case '\\':
                    escaped.append("\\\\");
                    break;
                case '\b':
                    escaped.append("\\b");
                    break;
                case '\f':
                    escaped.append("\\f");
                    break;
                case '\n':
                    escaped.append("\\n");
                    break;
                case '\r':
                    escaped.append("\\r");
                    break;
                case '\t':
                    escaped.append("\\t");
                    break;
                case '<':
                    escaped.append("\\u003c");
                    break;
                case '>':
                    escaped.append("\\u003e");
                    break;
                case '&':
                    escaped.append("\\u0026");
                    break;
                default:
                    if (character < 0x20 || character > 0x7e || character == '\u2028' || character == '\u2029') {
                        escaped.append(String.format(Locale.US, "\\u%04x", (int) character));
                    } else {
                        escaped.append(character);
                    }
            }
        }
        escaped.append('"');
        return escaped.toString();
    }

    private static final class NearestResult {
        private final Map<String, String> record;
        private final double distanceMiles;

        private NearestResult(Map<String, String> record, double distanceMiles) {
            this.record = record;
            this.distanceMiles = distanceMiles;
        }
    }

    private static final class Config {
        private final Path dataCentersCsv;
        private final Path powerPlantsCsv;
        private final Path nuclearPlantsCsv;
        private final Path waterSourcesCsv;
        private final Path outputJs;
        private final String generatedAt;

        private Config(
            Path dataCentersCsv,
            Path powerPlantsCsv,
            Path nuclearPlantsCsv,
            Path waterSourcesCsv,
            Path outputJs,
            String generatedAt
        ) {
            this.dataCentersCsv = dataCentersCsv;
            this.powerPlantsCsv = powerPlantsCsv;
            this.nuclearPlantsCsv = nuclearPlantsCsv;
            this.waterSourcesCsv = waterSourcesCsv;
            this.outputJs = outputJs;
            this.generatedAt = generatedAt;
        }

        private static Config parse(String[] args) {
            Path dataCentersCsv = DEFAULT_DATA_CENTERS;
            Path powerPlantsCsv = DEFAULT_POWER_PLANTS;
            Path nuclearPlantsCsv = DEFAULT_NUCLEAR_PLANTS;
            Path waterSourcesCsv = DEFAULT_WATER_SOURCES;
            Path outputJs = DEFAULT_OUTPUT_JS;
            String generatedAt = DateTimeFormatter.ofPattern("MMMM d, yyyy h:mm a", Locale.US)
                .format(LocalDateTime.now());

            for (int index = 0; index < args.length; index += 1) {
                String option = args[index];
                if (index + 1 >= args.length) {
                    throw new IllegalArgumentException("Missing value for " + option);
                }
                String value = args[index + 1];
                index += 1;

                switch (option) {
                    case "--data-centers":
                        dataCentersCsv = Path.of(value);
                        break;
                    case "--power-plants":
                        powerPlantsCsv = Path.of(value);
                        break;
                    case "--nuclear-plants":
                        nuclearPlantsCsv = Path.of(value);
                        break;
                    case "--water-sources":
                        waterSourcesCsv = Path.of(value);
                        break;
                    case "--output-js":
                        outputJs = Path.of(value);
                        break;
                    case "--generated-at":
                        generatedAt = cleanText(value);
                        break;
                    default:
                        throw new IllegalArgumentException("Unknown option: " + option);
                }
            }

            return new Config(
                dataCentersCsv,
                powerPlantsCsv,
                nuclearPlantsCsv,
                waterSourcesCsv,
                outputJs,
                generatedAt);
        }
    }
}
