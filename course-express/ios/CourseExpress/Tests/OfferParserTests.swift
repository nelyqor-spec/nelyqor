import XCTest
@testable import CourseExpress

/// Mêmes cas que backend/tests/parser.test.ts, sur les mêmes fixtures (fixtures/ocr).
final class OfferParserTests: XCTestCase {
    private func fixture(_ name: String) throws -> String {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: name, withExtension: "txt"), "fixture \(name) absente du bundle de test")
        return try String(contentsOf: url, encoding: .utf8)
    }

    func testUberPizzaCosy() throws {
        let r = try OfferParser.parse(fixture("uber_pizza_cosy"))
        XCTAssertEqual(r.platform, .uberEats)
        XCTAssertEqual(r.restaurantName, "Pizza Cosy - Aix en Provence")
        XCTAssertEqual(r.customerAddressRaw, "230 Av. de Bagatelle, 13090, Aix-en-Provence")
        XCTAssertEqual(r.customerAddress, "230 avenue de Bagatelle, 13090 Aix-en-Provence, France")
    }

    func testRestaurantNotConfusedWithAddressOrMapLabel() throws {
        let r = try OfferParser.parse(fixture("uber_pizza_cosy"))
        XCTAssertNotEqual(r.restaurantName, r.customerAddressRaw)
        XCTAssertNotEqual(r.restaurantName, "Pizza")
    }

    func testDistanceNeverReusedAndNoRestaurantAddress() throws {
        let r = try OfferParser.parse(fixture("uber_pizza_cosy"))
        let all = "\(r.restaurantName)|\(r.customerAddress)|\(r.customerAddressRaw)"
        XCTAssertFalse(all.contains("0.9"))
        XCTAssertFalse(all.lowercased().contains("route de berre"))
    }

    func testOneLineAndMultiLineAddressesMatch() throws {
        XCTAssertEqual(try OfferParser.parse(fixture("uber_pizza_cosy_one_line")).customerAddress,
                       try OfferParser.parse(fixture("uber_pizza_cosy")).customerAddress)
    }

    func testMissingAddress() throws {
        XCTAssertThrowsError(try OfferParser.parse(fixture("uber_no_address"))) { XCTAssertEqual($0 as? OfferParseError, .missingAddress) }
        XCTAssertThrowsError(try OfferParser.parse("")) { XCTAssertEqual($0 as? OfferParseError, .noText) }
    }

    func testSyntheticDeliveroo() throws {
        let a = try OfferParser.parse(fixture("SYNTHETIQUE_deliveroo_1"))
        XCTAssertEqual(a.platform, .deliveroo)
        XCTAssertEqual(a.restaurantName, "Le Petit Café Crème")
        XCTAssertEqual(a.customerAddress, "12 boulevard du Roi René, 13100 Aix-en-Provence, France")
        let b = try OfferParser.parse(fixture("SYNTHETIQUE_deliveroo_2"))
        XCTAssertEqual(b.restaurantName, "Sushi Shop - Aix Sextius")
        XCTAssertEqual(b.customerAddress, "4 route de Galice, 13090 Aix-en-Provence, France")
    }

    func testNormalization() {
        XCTAssertEqual(OfferParser.normalizeAddress("230 Av. de Bagatelle, 13090, Aix en Provence"), "230 avenue de Bagatelle, 13090 Aix-en-Provence, France")
        XCTAssertEqual(OfferParser.normalizeAddress("7 Ch. des Pinchinats, 13100 Aix-en-Provence"), "7 chemin des Pinchinats, 13100 Aix-en-Provence, France")
        XCTAssertEqual(OfferParser.normalizeAddress("3 Rue St Jean, 13002 Marseille, France"), "3 Rue Saint Jean, 13002 Marseille, France")
    }

    func testDisplayMinutesConsistency() {
        for (a, b) in [(120, 420), (89, 89), (61, 61), (29, 29), (150, 150), (0, 0)] {
            let d = DisplayMinutes.from(leg1Seconds: a, leg2Seconds: b)
            XCTAssertEqual(d.toRestaurantMinutes + d.restaurantToCustomerMinutes, d.totalMinutes)
            XCTAssertEqual(d.totalMinutes, Int((Double(a + b) / 60).rounded()))
        }
        XCTAssertEqual(EstimateResult.banner(display: .from(leg1Seconds: 120, leg2Seconds: 420), mock: false),
                       "🚗 Restaurant 2 min · Client 7 min · Total 9 min")
        XCTAssertTrue(EstimateResult.banner(display: .from(leg1Seconds: 120, leg2Seconds: 420), mock: true).hasPrefix("[DÉMO"))
    }
}

/// OCR Vision réel sur la vraie capture Uber (fixtures/uber_offre_pizza_cosy.jpeg). À lancer sur simulateur ou iPhone.
final class OfferParserFixtureImageTests: XCTestCase {
    func testRealUberScreenshotThroughVision() throws {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "uber_offre_pizza_cosy", withExtension: "jpeg"))
        let text = try ScreenshotOCR().recognize(from: Data(contentsOf: url))
        let r = try OfferParser.parse(text)
        XCTAssertEqual(r.restaurantName, "Pizza Cosy - Aix en Provence")
        XCTAssertTrue(r.customerAddress.hasPrefix("230 avenue de Bagatelle"), r.customerAddress)
        XCTAssertTrue(r.customerAddress.contains("13090"))
    }
}
