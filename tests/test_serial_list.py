"""Board serial lists in answers: contiguous runs become a range."""
import unittest

from service.query_answers import _serial_list


class SerialListTests(unittest.TestCase):
    def test_contiguous_run_with_digits_in_the_suffix(self):
        serials = [f"ORD-26041-01-B{i:03d}-0JE" for i in range(30, 0, -1)]
        self.assertEqual(_serial_list(serials), "ORD-26041-01-B001-0JE – ORD-26041-01-B030-0JE")

    def test_gaps_and_short_lists(self):
        gap = [f"R-B{i:03d}-X" for i in (1, 2, 3, 5, 6)]
        self.assertEqual(_serial_list(gap), "R-B001-X, R-B002-X, R-B003-X, R-B005-X 외 1대")
        self.assertEqual(_serial_list(["X1", "X2"]), "X1, X2")


if __name__ == "__main__":
    unittest.main()
