import { Shape, Kind } from "./shapes";
import { circleArea } from "./geometry";

// `implements Shape` -> is_implementation relationship; method `area` calls the
// cross-file `circleArea` -> a `calls` edge resolved via the enclosing range.
export class Circle implements Shape {
  kind: Kind = Kind.Round;

  constructor(private radius: number) {}

  area(): number {
    return circleArea(this.radius);
  }
}
